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
  for (let i = 0; i < askable.length; i += KIS_PER_SECOND) {
    if (i > 0) await sleep(1_000);
    const wave = askable.slice(i, i + KIS_PER_SECOND);
    calls += wave.length;
    const results = await Promise.all(wave.map(async ([sym, code]) => {
      const url = `${KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-price?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD=${code}`;
      try {
        const response = await fetch(url, { headers: head, cache: "no-store", signal: AbortSignal.timeout(KIS_TIMEOUT_MS) });
        if (!response.ok) {
          errors.add(`KIS HTTP ${response.status}`);
          return [sym, null] as const;
        }
        const body = (await response.json()) as { rt_cd?: unknown; msg_cd?: unknown };
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
  return { quotes, failed, calls, error: errors.size ? [...errors].join(", ") : null };
}
