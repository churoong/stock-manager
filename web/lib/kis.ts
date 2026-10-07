/**
 * 한국투자증권 KIS Open API — 국내 장중 현재가 (docs/intraday.md 1.1, docs/data-sources.md 3, docs/infra.md 25.983).
 *
 * 2026-09-16 에 "클라우드 IP 에서 불리는지 모른다" 며 미뤘다. 2026-10-07 사용자가 키를 넣었고, 같은 날 Actions 러너에서
 * 토큰 발급·현재가·휴장일 조회가 IP 등록 없이 됐다(실행 37577384577, 각 0.5~0.7초). 야후 국내 시세는 약 20분 늦다.
 *
 * - **시세 조회만** 부른다. 주문 API 는 부르지 않는다(CLAUDE.md: 자동 매매 없음). 계좌번호도 쓰지 않는다
 * - **토큰은 하루 한 번** 받아 DB(`api_tokens`)에 둔다. 발급은 1분에 1회 제한이고 유효 24시간이다. 5분 크론마다 받으면 안 된다
 * - **실패하면 야후로 돌아간다.** KIS 가 없어도(키 없음·표 없음·HTTP 오류) 장중 감시는 예전처럼 돈다(CLAUDE.md "없어도 전 기능이 동작")
 * - 시세는 본인 투자 목적으로만 쓴다(KIS 이용 조건). 텔레그램 본인 알림과 본인 화면뿐이다
 */

import { batch, execute, rowsToObjects } from "@/lib/db";
import type { Quote } from "@/lib/intraday";

export const KIS_SOURCE = "kis_openapi";
export const KIS_BASE = "https://openapi.koreainvestment.com:9443";
/** 국내주식 현재가 시세 (실측 2026-10-07: rt_cd=0, stck_prpr·prdy_ctrt·acml_vol) */
export const KIS_PRICE_TR = "FHKST01010100";
/** 실전 REST 초당 20건(공식). 여유를 두고 한 묶음 15건씩, 묶음 사이 1초 */
export const KIS_PER_SECOND = 15;
/** 토큰이 이만큼 남았으면 새로 받는다. 발급 직후 6시간 안 재발급은 같은 토큰을 준다(공식) */
export const KIS_TOKEN_REFRESH_MS = 60 * 60_000;
const KIS_TIMEOUT_MS = 5_000;
/**
 * KIS 에 쓰는 전체 시간 (25.986, 교차검증). 묶음마다 5초씩 매달리면 45종목에 17초가 지나서야 야후를 묻고, DART 예산(15초)이 사라지고
 * 함수 상한 30초에 걸려 그 호출의 알림이 통째로 사라질 수 있었다. 다음 묶음이 **최악(제한 시간 5초)에도 이 안에 끝날 때만** 부르고,
 * 아니면 남은 종목은 바로 야후로 보낸다 — 한 묶음이 매달리면(5초) 거기서 멈춘다. 정상(묶음 0.5초)이면 4묶음·60종목까지 KIS 로 받는다
 */
export const KIS_DEADLINE_MS = 10_000;
/** 토큰이 무효·만료라는 KIS 오류 코드 머리 (EGW00121 유효하지 않은 token · EGW00123 만료 등) `[확인필요: 전체 목록]` */
export const KIS_TOKEN_ERROR_PREFIX = "EGW0012";
const TOKEN_NAME = "kis";

export function kisConfigured(env: Record<string, string | undefined> = process.env): boolean {
  return Boolean(env.KIS_APP_KEY && env.KIS_APP_SECRET);
}

/** 야후 심볼 → KIS 단축코드. 국내(.KS·.KQ)의 6자리만 — 나머지는 KIS 로 묻지 않는다 */
export function kisCode(yahooSymbol: string): string | null {
  const m = /^([0-9A-Z]{6})\.(KS|KQ)$/.exec(yahooSymbol.toUpperCase());
  return m ? m[1] : null;
}

/** 토큰을 그대로 써도 되나 — 만료까지 `KIS_TOKEN_REFRESH_MS` 넘게 남았나 */
export function tokenFresh(expiresAt: string | null | undefined, now: Date): boolean {
  if (!expiresAt) return false;
  const t = Date.parse(expiresAt);
  return Number.isFinite(t) && t - now.getTime() > KIS_TOKEN_REFRESH_MS;
}

const num = (v: unknown): number | null => {
  const n = typeof v === "string" ? Number(v.replace(/,/g, "")) : typeof v === "number" ? v : NaN;
  return Number.isFinite(n) ? n : null;
};

/**
 * 현재가 응답 → `Quote`. 값이 없거나 0 이하이면 null (야후 `parseSpark` 와 같은 규칙, 25.721).
 *
 * - 전일 종가는 **기준가**(`stck_sdpr`)다 — 권리락·분할 날에는 조정된 값이라 `actionSuspect` 가 DB 전일 종가와 견줘 기업행위를 알아챈다.
 *   없으면 현재가 − 전일 대비(`prdy_vrss`)
 * - 응답에 시세 시각이 없다. 실시간이라 **부른 시각**을 쓴다 — 장 밖 호출은 경로가 먼저 걸러 낸다
 * - **거래정지(`temp_stop_yn`=Y)이거나 오늘 체결이 없으면(시가 0·거래량 0) null** (25.986, 교차검증). 부른 시각을 붙이면 정지 종목의
 *   지난 가격이 "이번 장 시세" 로 통과해(25.196 의 지난 장 가드를 우회) 손절가 아래에서 정지된 보유 종목에 매일 "손절선 터치" 가 나갔다.
 *   null 이면 야후로 넘어가고, 야후는 지난 장 시각을 줘 예전처럼 판정하지 않는다
 */
export function parseKisPrice(yahooSymbol: string, payload: unknown, at: Date): Quote | null {
  const body = payload as { rt_cd?: unknown; output?: Record<string, unknown> } | null;
  if (!body || String(body.rt_cd) !== "0" || !body.output) return null;
  const o = body.output;
  const price = num(o.stck_prpr);
  if (price === null || price <= 0) return null;
  const pos = (v: unknown) => {
    const n = num(v);
    return n !== null && n > 0 ? n : null;
  };
  const diff = num(o.prdy_vrss);
  const volume = num(o.acml_vol);
  if (String(o.temp_stop_yn ?? "").toUpperCase() === "Y") return null;
  if ((num(o.stck_oprc) ?? 0) <= 0 && (volume ?? 0) <= 0) return null;
  return {
    symbol: yahooSymbol,
    price,
    time: at.toISOString(),
    previous_close: pos(o.stck_sdpr) ?? (diff !== null && price - diff > 0 ? price - diff : null),
    day_high: pos(o.stck_hgpr),
    day_low: pos(o.stck_lwpr),
    // 거래량 0 은 "아직 체결 없음" 이라 값이다 (parseSpark 와 같다)
    volume: volume !== null && volume >= 0 ? volume : null,
    source: KIS_SOURCE,
  };
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** DB 에 둔 토큰, 없거나 곧 끝나면 새로 받아 둔다. 표가 없으면(마이그레이션 전) 던진다 — 부른 쪽이 야후로 돌아간다 */
async function accessToken(now: Date): Promise<{ token: string; issued: boolean }> {
  const row = rowsToObjects<{ token: string; expires_at: string }>(
    await execute("SELECT token, expires_at FROM api_tokens WHERE name = ?", [TOKEN_NAME]),
  )[0];
  if (row && tokenFresh(row.expires_at, now)) return { token: row.token, issued: false };
  const response = await fetch(`${KIS_BASE}/oauth2/tokenP`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ grant_type: "client_credentials", appkey: process.env.KIS_APP_KEY, appsecret: process.env.KIS_APP_SECRET }),
    cache: "no-store",
    signal: AbortSignal.timeout(KIS_TIMEOUT_MS),
  });
  const body = (await response.json().catch(() => ({}))) as { access_token?: string; expires_in?: number; error_code?: string };
  if (!response.ok || !body.access_token) {
    // 값은 남기지 않는다 — 오류 코드만
    throw new Error(`KIS 토큰 HTTP ${response.status}${body.error_code ? ` ${body.error_code}` : ""}`);
  }
  const expires = new Date(now.getTime() + Number(body.expires_in ?? 86_400) * 1000).toISOString();
  await batch([{
    sql: `INSERT INTO api_tokens (name, token, expires_at, source, fetched_at) VALUES (?, ?, ?, ?, ?)
          ON CONFLICT (name) DO UPDATE SET token = excluded.token, expires_at = excluded.expires_at, fetched_at = excluded.fetched_at`,
    args: [TOKEN_NAME, body.access_token, expires, KIS_SOURCE, now.toISOString()],
  }]);
  return { token: body.access_token, issued: true };
}

export interface KisResult {
  quotes: Record<string, Quote>;
  /** KIS 로 못 받은 심볼 — 부른 쪽이 야후로 다시 묻는다 */
  failed: string[];
  /** 실제로 나간 KIS 호출 수 (토큰 발급 포함) */
  calls: number;
  /** 무엇이 안 됐나 (값 없음). 비면 다 받았다 */
  error: string | null;
}

/** 국내 심볼들의 현재가. 초당 `KIS_PER_SECOND` 건씩 묶어 부른다 */
export async function fetchKisQuotes(symbols: string[], now: Date = new Date()): Promise<KisResult> {
  const codes = symbols.map((s) => [s, kisCode(s)] as const);
  const askable = codes.filter((c): c is readonly [string, string] => c[1] !== null);
  const failed = codes.filter((c) => c[1] === null).map((c) => c[0]);
  const quotes: Record<string, Quote> = {};
  if (!askable.length) return { quotes, failed, calls: 0, error: null };
  let calls = 0;
  let token: string;
  try {
    const got = await accessToken(now);
    token = got.token;
    if (got.issued) calls += 1;
  } catch (error) {
    const message = error instanceof Error ? error.message : "KIS 토큰 실패";
    return { quotes, failed: symbols, calls, error: /no such table/i.test(message) ? "KIS 토큰 표 없음(마이그레이션 전)" : message };
  }
  const head = {
    authorization: `Bearer ${token}`, appkey: process.env.KIS_APP_KEY ?? "", appsecret: process.env.KIS_APP_SECRET ?? "",
    custtype: "P", tr_id: KIS_PRICE_TR,
  };
  const errors = new Set<string>();
  const started = Date.now();
  let tokenBad = false;
  for (let i = 0; i < askable.length; i += KIS_PER_SECOND) {
    // 시간이 다 됐거나 토큰이 무효면 남은 것은 야후로 (25.986)
    if (tokenBad || Date.now() - started + (i > 0 ? 1_000 : 0) + KIS_TIMEOUT_MS > KIS_DEADLINE_MS) {
      if (!tokenBad) errors.add("KIS 시간 초과 — 남은 종목은 야후로");
      failed.push(...askable.slice(i).map(([sym]) => sym));
      break;
    }
    if (i > 0) await sleep(1_000);
    const wave = askable.slice(i, i + KIS_PER_SECOND);
    calls += wave.length;
    const results = await Promise.all(wave.map(async ([sym, code]) => {
      const url = `${KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-price?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD=${code}`;
      try {
        const response = await fetch(url, { headers: head, cache: "no-store", signal: AbortSignal.timeout(KIS_TIMEOUT_MS) });
        // 오류 응답에도 본문(msg_cd)이 있다 — 토큰 무효를 가려내려고 읽는다 (25.986)
        const body = (await response.json().catch(() => ({}))) as { rt_cd?: unknown; msg_cd?: unknown };
        if (String(body.msg_cd ?? "").startsWith(KIS_TOKEN_ERROR_PREFIX)) tokenBad = true;
        if (!response.ok) {
          errors.add(`KIS HTTP ${response.status}${body.msg_cd ? ` ${String(body.msg_cd)}` : ""}`);
          return [sym, null] as const;
        }
        const quote = parseKisPrice(sym, body, new Date());
        if (!quote && String(body.rt_cd) !== "0") errors.add(`KIS ${String(body.msg_cd ?? "오류")}`);
        return [sym, quote] as const;
      } catch (error) {
        errors.add(error instanceof Error ? `KIS ${error.name}` : "KIS 호출 실패");
        return [sym, null] as const;
      }
    }));
    for (const [sym, quote] of results) {
      if (quote) quotes[sym] = quote;
      else failed.push(sym);
    }
  }
  if (tokenBad) {
    // **무효 토큰을 버린다** (25.986, 교차검증). 키를 바꿨거나 KIS 가 토큰을 무효로 하면, 만료 1시간 전까지(최대 23시간) 같은 토큰을
    // 계속 써 KIS 가 하루 내내 조용히 죽어 있었다. 지우면 다음 호출(5분 뒤)이 새로 받는다
    errors.add("KIS 토큰 무효 — 지우고 다음 호출에 새로 받음");
    await execute("DELETE FROM api_tokens WHERE name = ?", [TOKEN_NAME]).catch(() => undefined);
  }
  return { quotes, failed, calls, error: errors.size ? [...errors].join(", ") : null };
}

/** 체결강도(`tday_rltv`) — 매수 체결량 ÷ 매도 체결량 × 100. 100 넘으면 매수세가 세다 (25.991) */
export const KIS_CCNL_TR = "FHKST01010300";
/** 한 호출에서 체결강도를 묻는 최대 종목 수 — 알림이 몰리는 날에도 시간을 크게 쓰지 않게 */
export const KIS_STRENGTH_MAX = 10;

export function strengthNote(v: number): string {
  const 쪽 = v > 100 ? "매수 우위" : v < 100 ? "매도 우위" : "균형";
  return `체결강도 ${v.toFixed(1)} (${쪽})`;
}

/** 체결 응답의 첫 줄(가장 최근 체결)의 체결강도. 없으면 null */
export function parseStrength(payload: unknown): number | null {
  const body = payload as { rt_cd?: unknown; output?: Array<Record<string, unknown>> } | null;
  if (!body || String(body.rt_cd) !== "0" || !Array.isArray(body.output) || !body.output.length) return null;
  const v = num(body.output[0].tday_rltv);
  return v !== null && v > 0 ? v : null;
}

/**
 * 새로 나갈 매수 구간·관심 목표가 알림에 붙일 체결강도 (docs/intraday.md 1.1, 25.991). 실패하면 빈 결과 — 알림은 그대로 나간다.
 * 토큰은 시세와 같은 `api_tokens` 줄을 쓴다(이미 받아 둔 것). 반환: 야후 심볼 → 체결강도, 나간 호출 수
 */
export async function fetchKisStrengths(symbols: string[], now: Date = new Date()): Promise<{ values: Record<string, number>; calls: number }> {
  const values: Record<string, number> = {};
  const 대상 = symbols.map((s) => [s, kisCode(s)] as const).filter((c): c is readonly [string, string] => c[1] !== null).slice(0, KIS_STRENGTH_MAX);
  if (!대상.length) return { values, calls: 0 };
  let token: string;
  let calls = 0;
  try {
    const got = await accessToken(now);
    token = got.token;
    if (got.issued) calls += 1;
  } catch {
    return { values, calls };
  }
  const head = {
    authorization: `Bearer ${token}`, appkey: process.env.KIS_APP_KEY ?? "", appsecret: process.env.KIS_APP_SECRET ?? "",
    custtype: "P", tr_id: KIS_CCNL_TR,
  };
  calls += 대상.length;
  await Promise.all(대상.map(async ([sym, code]) => {
    try {
      const url = `${KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-ccnl?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD=${code}`;
      const response = await fetch(url, { headers: head, cache: "no-store", signal: AbortSignal.timeout(KIS_TIMEOUT_MS) });
      const v = parseStrength(await response.json().catch(() => null));
      if (v !== null) values[sym] = v;
    } catch {
      // 곁다리다 — 체결강도 없이 알림은 나간다
    }
  }));
  return { values, calls };
}
