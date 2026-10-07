import { NextResponse } from "next/server";
import { notifyQuotaOnce } from "@/lib/quotaNotice";
import { batch, execute, quotaReason, rowsToObjects } from "@/lib/db";
import {
  SPARK_MAX_SYMBOLS,
  activeSession,
  alertGroups,
  bundleMessage,
  chunk,
  evaluate,
  inQuietHours,
  isSessionQuote,
  parseSpark,
  staleTargetsNote,
  tokenMatches,
  targetsFreshFor,
  DISCLOSURE_ALERTS_FOR,
  ACTION_GUARD_ROWS,
  recentActionKr,
  actionSuspect,
  watchPriceUsable,
  dropOnActionSuspect,
  recentActionUs,
  withoutSignalLevels,
  type Hit,
  type Market,
  type Quote,
  disclosureHit,
  type Session,
  type Target,
  heartbeatOutcome,
  intradayOutcome,
  DART_KEY_MISSING,
  secretWeakDetail,
  withinOpenGrace,
  freshDisclosures,
} from "@/lib/intraday";
import { ALERT_UPSERT } from "@/lib/alertWrite";
import { DART_API_NAME, DART_DAILY_LIMIT, addUsage, isBlocked, readUsage } from "@/lib/apiUsage";
import { KIS_SOURCE, fetchKisQuotes, kisConfigured } from "@/lib/kis";
import { localDate } from "@/lib/market";
import { SAFE_LEN, sendTelegram } from "@/lib/telegram";
import { recordHeartbeat } from "@/lib/heartbeat";
import { readSetting, type Settings as AppSettings } from "@/lib/settings";
import { watchTursoReturn } from "@/lib/tursoWatch";

/**
 * 장중 모니터링 (docs/intraday.md, Step 14). cron-job.org 가 정규장 동안 5분마다 부른다.
 *
 *   GET /api/cron/intraday?market=KR   헤더 x-cron-secret: <CRON_SECRET>
 *
 * 순서: 토큰 확인 → 조용시간이 아니면 밀린 알림부터 보냄 → 장 밖이면 끝 → 시세 → 판정 → alerts 삽입
 *       (같은 종목·트리거·날짜는 DB 유니크로 하루 한 번) → 조용시간이 아니면 새 알림 발송
 *
 * **쓰기 대상은 다섯이다** — alerts(하는 일 자체) · cron_heartbeats(호출 기록) · api_usage(DART·KIS 한도
 * 카운터, 25.105·25.983) · settings 한 줄(Turso 복귀 표시, `watchTursoReturn()`, 25.12) · api_tokens(KIS 접근토큰 한 줄, 25.983).
 * 점수·신호·매매·보유는 건드리지 않는다. 2026-09-26 까지 이 줄은 "읽고 alerts 만 쓴다" 였다 —
 * 25.191 이 docs/intraday.md 는 고치고 이 머리말은 놓쳤다(docs/infra.md 25.195).
 * 로그인 없이 부르는 경로라 proxy.ts 가 통과시키고, 여기서 토큰으로 스스로를 지킨다.
 */
export const dynamic = "force-dynamic";

/** 조용시간에 생겨 보내지 않기로 한 알림의 sent_at 표시 */
const QUIET_SKIPPED = "quiet-skipped";
/** 보내려고 잡은 알림의 표시 (25.538). 이 시간이 지나도 잡힌 채면 잡은 호출이 죽은 것 — 다시 대기로 본다 */
const CLAIM_PREFIX = "claim:";
const CLAIM_STALE_MS = 10 * 60_000;
/** 보낸 뒤 발송 표시를 쓰는 시도 횟수 (25.551) */
const MARK_SENT_TRIES = 3;
/** 발송 표시 재시도 간격(ms, 시도마다 늘림). 경로 제한 시간 안에서 최대 1.5초 */
const MARK_SENT_BACKOFF_MS = 500;

type Settings = {
  alert_thresholds: AppSettings["alert_thresholds"];
  quiet_hours: AppSettings["quiet_hours"];
  /** 범위 밖이라 기본값으로 되돌린 것들 (docs/infra.md 25.172) */
  warnings: string[];
};

/**
 * **읽을 때도 스키마로 본다** (docs/infra.md 25.172).
 *
 * `settings.ts` 의 검증은 **쓸 때**만 돈다. 되살리기·이주가 써 넣은 값은 그 문을
 * 안 지난다. `spike_pct` 가 0.001 이면 **모든 종목이 매 5분 급등락으로 걸리고**,
 * 텔레그램 429 로 이어진다 — 2026-09-20 에 실제로 겪은 일이다(25.38).
 *
 * 기본값은 `DEFAULT_SETTINGS` 하나를 쓴다. 여기 손으로 적으면 두 곳이 갈라진다.
 */
async function readSettings(): Promise<Settings> {
  const rows = rowsToObjects<{ key: string; value: string }>(
    await execute("SELECT key, value FROM settings WHERE key IN ('alert_thresholds', 'quiet_hours')"),
  );
  const raw: Record<string, unknown> = {};
  for (const r of rows) {
    try {
      raw[r.key] = JSON.parse(r.value);
    } catch {
      // 깨진 JSON 은 없는 것으로 본다. 아래에서 기본값이 들어간다
    }
  }
  const 문턱 = readSetting("alert_thresholds", raw.alert_thresholds);
  const 조용 = readSetting("quiet_hours", raw.quiet_hours);
  return {
    alert_thresholds: 문턱.value,
    quiet_hours: 조용.value,
    warnings: [문턱.warning, 조용.warning].filter((w): w is string => w !== null),
  };
}

/** 마지막 호출 기록 (docs/intraday.md 7장). 토큰이 맞은 호출만 남는다. 실패해도 본 작업을 막지 않는다. */

/**
 * 보내지 않은 알림을 한 통으로 보내고 sent_at 을 찍는다. 실패하면 다음 호출이 다시 시도한다.
 *
 * **50건이 4,096자를 넘을 수 있다.** 긴 종목명 + 가격이 든 줄이 50개면 넘는다. 보낸 뒤에야
 * sent_at 을 찍으므로, 거절당하면 같은 50건이 영원히 남아 5분마다 같은 실패를 되풀이한다.
 * `sendTelegram` 이 나눠 보내는 것이 그것을 막는다 (docs/infra.md 25.58).
 */
/** 한 통에 묶는 알림 수. 아래 질의의 `LIMIT 50` 과 같아야 한다 */
const FLUSH_BATCH = 50;
/**
 * 한 호출에서 보내는 묶음 수의 상한 (docs/infra.md 25.253). 조용시간 해제 호출(07:00)은 **장 밖**이라 이 한 번뿐이다 —
 * 예전에는 50건만 보내고 끝나 나머지가 다음 호출(평일 09:00, 주말은 더 늦게)까지 남았다. 급락장 하룻밤이면 50건은 쉽게 넘는다.
 * 10통(500건)이면 밤새 쌓일 수 있는 양을 넉넉히 덮는다.
 *
 * **통 사이를 띄운다** (docs/infra.md 25.347). 예전 주석은 "텔레그램 초당 한도(대화 하나에 초당 1통 안팎 `[확인필요]`)와도
 * 멀다" 고 적었는데, 그 한도가 맞다면 10통을 쉬지 않고 보내는 것은 한도에 **닿는** 쪽이다. 중간에 429 가 나면 남은 묶음은
 * 다음 호출로 밀리고, 07:00 해제 호출은 한 번뿐이라 평일 09:00 까지 밀린다 — 25.253 이 막으려던 바로 그 상황이다.
 */
const FLUSH_MAX_ROUNDS = 10;
/** 통 사이 간격(ms). 대화 하나에 초당 1통 안팎이라는 한도 `[확인필요]` 에 맞춘다. 10통이면 최대 약 10초 */
const FLUSH_GAP_MS = 1_100;
/**
 * 이 경로가 쓸 수 있는 실행 시간(초). 통 사이 간격(최대 약 10초)과 시세·DART 호출을 더해도 넉넉하게.
 * Vercel Hobby 가 허용하는 범위 안이다 `[확인필요: Hobby 함수 실행 시간 상한]`
 */
export const maxDuration = 30;

async function flushPending(now: Date, notes: string[] = []): Promise<number> {
  let 보낸수 = 0;
  for (let 차례 = 0; 차례 < FLUSH_MAX_ROUNDS; 차례++) {
    if (차례 > 0) await new Promise((r) => setTimeout(r, FLUSH_GAP_MS));
    // 머리말(목록 날짜·설정 경고)은 **첫 통에만** 붙인다
    const n = await flushOnce(now, 차례 === 0 ? notes : [], 차례 === 0);
    보낸수 += n;
    if (n < FLUSH_BATCH) break;
  }
  return 보낸수;
}

async function flushOnce(now: Date, notes: string[], 머리말: boolean): Promise<number> {
  // **먼저 잡고 보낸다** (docs/infra.md 25.538, 감사 재현). 호출 둘이 겹치면(크론 중복·수동 실행) 같은 대기 알림을 둘 다
  // 읽어 두 번 보냈다. `claim:시각` 으로 잡은 행만 보낸다. 잡고 죽으면 CLAIM_STALE_MS 뒤 다시 대기로 본다
  const stamp = now.toISOString();
  const 묵은잡음 = `${CLAIM_PREFIX}${new Date(now.getTime() - CLAIM_STALE_MS).toISOString()}`;
  const 후보 = rowsToObjects<{ id: number; market: string; message: string; created_at: string; sent_at: string | null }>(
    // `LIMIT 50` 은 `FLUSH_BATCH` 와 같다. 질의에 값을 끼우지 않는다(웹의 보간 질의 검사)
    await execute(
      // 잡은 행은 `'claim:'` 이상 범위로 찾는다 (docs/infra.md 25.595, 감사). `sent_at < 'claim:…'` 만 쓰면 ISO 시각('2026-…')이
      // 'claim' 보다 작아 **이미 보낸 행 전부**를 읽었다 — 알림은 지우지 않으니 호출마다 읽는 행이 계속 늘었다
      "SELECT id, market, message, created_at, sent_at FROM alerts WHERE sent_at IS NULL"
        + " OR (sent_at >= 'claim:' AND sent_at < ?) ORDER BY created_at LIMIT 50",
      [묵은잡음],
    ),
  );
  if (후보.length === 0) return 0;
  const 잡은것 = await batch(후보.map((p) => ({
    sql: "UPDATE alerts SET sent_at = ? WHERE id = ? AND COALESCE(sent_at, '') = ?",
    args: [`${CLAIM_PREFIX}${stamp}`, p.id, p.sent_at ?? ""],
  })));
  const pending = 후보.filter((_p, i) => (잡은것[i]?.affectedRows ?? 0) > 0);
  if (pending.length === 0) return 0;
  // **감시 목록이 언제 만들어진 것인지 말한다** (docs/infra.md 25.162). `built_at` 은
  // 처음부터 NOT NULL 로 적고 있었는데 아무도 읽지 않았다. 읽기 한 번이 늘지만
  // 보낼 알림이 있을 때만 돈다
  type Built = { market: string; built_at: string | null };
  const built = !머리말
    ? ([] as Built[])
    : await execute("SELECT market, MAX(built_at) AS built_at FROM monitor_targets GROUP BY market")
        .then((rs) => rowsToObjects<Built>(rs))
        // 표가 아직 없거나 못 읽어도 **알림은 나가야 한다.** 말 한 줄 때문에 막지 않는다
        .catch(() => [] as Built[]);
  // 붙일 말이 여럿이면 줄로 잇는다. **머리에 한 번만** 나온다 (25.162)
  const 말들 = [staleTargetsNote(built, now), ...notes].filter((n): n is string => Boolean(n));
  const 머리 = 말들.length ? 말들.join("\n") : null;
  const 묶음들 = alertGroups(pending, now, 머리, SAFE_LEN);
  let 보낸 = 0;
  for (const [순번, 묶음] of 묶음들.entries()) {
    try {
      await sendTelegram(bundleMessage(묶음, now, 순번 === 0 ? 머리 : null));
    } catch (e) {
      // **보냈는지 몰라도(응답 전 시간 초과) 푼다** (docs/infra.md 25.618, 교차검증 — 25.613 을 되돌림). 10초 제한은 DNS·TCP·TLS
      // 까지 덮어 "요청은 나갔다" 를 가르지 못한다. 손절·목표 알림은 **빠지는 편이 두 번 가는 편보다 나쁘다**
      // (응답 헤더 200 뒤의 시간 초과는 `sendTelegram` 이 보낸 것으로 본다)
      // 못 보낸 묶음(이것과 뒤의 것)은 잡은 것을 풀어 다음 호출이 보낸다 — 앞 묶음은 이미 갔으니 두지 않는다
      const 남은 = 묶음들.slice(순번).flat();
      await batch(남은.map((p) => ({ sql: "UPDATE alerts SET sent_at = NULL WHERE id = ?", args: [p.id] }))).catch(() => undefined);
      throw e;
    }
    // 보낸 뒤 표시가 실패하면 잡은 채 남아 CLAIM_STALE_MS 뒤 **같은 알림이 다시 나갔다** (25.551). 한 번의 순간 실패로
    // 두 번 가지 않게 몇 번 더 시도한다. 끝내 못 적으면 그때 던진다(다시 가는 쪽이 안 가는 쪽보다 낫다)
    const 표시 = 묶음.map((p) => ({ sql: "UPDATE alerts SET sent_at = ? WHERE id = ?", args: [stamp, p.id] }));
    for (let 시도 = 1; ; 시도++) {
      try {
        await batch(표시);
        break;
      } catch (e) {
        if (시도 >= MARK_SENT_TRIES) throw e;
        // 잠깐 쉬고 다시 — 연달아 부르면 몇 백 ms 이어지는 429·503 에 셋 다 걸렸다 (25.555, 교차검증)
        await new Promise((r) => setTimeout(r, 시도 * MARK_SENT_BACKOFF_MS));
      }
    }
    보낸 += 묶음.length;
  }
  return 보낸;
}

/** 외부 호출 제한 시간 (25.595). 함수 상한 30초 안에 야후 몇 묶음 + DART 여러 종목 + 발송이 들어가야 한다 [확인필요: 실측 응답 시간] */
const QUOTE_TIMEOUT_MS = 8_000;
const DART_TIMEOUT_MS = 4_000;
/** 호출 시작부터 DART 확인을 이어 가도 되는 시간. 뒤에 저장·발송(텔레그램 10초)·기록이 남는다 (25.598) */
const DART_BUDGET_MS = 15_000;

async function fetchYahooQuotes(symbols: string[]): Promise<{ quotes: Record<string, Quote>; errors: string[] }> {
  const quotes: Record<string, Quote> = {};
  const errors: string[] = [];
  for (const group of chunk(symbols, SPARK_MAX_SYMBOLS)) {
    const url = `https://query1.finance.yahoo.com/v7/finance/spark?symbols=${encodeURIComponent(group.join(","))}&range=1d&interval=5m`;
    try {
      // 제한 시간을 둔다 — 매달리면 함수가 30초 상한에 죽어 그 호출의 모든 알림이 사라졌다 (docs/infra.md 25.595, 감사)
      const response = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0" }, cache: "no-store", signal: AbortSignal.timeout(QUOTE_TIMEOUT_MS) });
      if (!response.ok) {
        errors.push(`야후 HTTP ${response.status}`);
        continue;
      }
      Object.assign(quotes, parseSpark(await response.json()));
    } catch (error) {
      errors.push(error instanceof Error ? error.message : "야후 호출 실패");
    }
  }
  return { quotes, errors };
}

/**
 * 시세. **국내는 KIS 실시간을 먼저** 묻고(docs/infra.md 25.983, 2026-10-07 사용자가 키를 넣음), 못 받은 종목만 야후(약 20분 지연)로 묻는다.
 * KIS 가 통째로 안 되면(키 없음·표 없음·HTTP 오류) 예전처럼 야후만 쓴다. `errors` 에는 **야후 실패만** 넣는다 — KIS 가 실패해도 야후가
 * 받았으면 판정은 됐다. KIS 쪽 사정은 `kis` 에 따로 남긴다
 */
async function fetchQuotes(
  symbols: string[], market: Market, now: Date,
): Promise<{ quotes: Record<string, Quote>; errors: string[]; kis: Record<string, unknown> | null }> {
  if (market !== "KR" || !kisConfigured() || !symbols.length) return { ...(await fetchYahooQuotes(symbols)), kis: null };
  const kis = await fetchKisQuotes(symbols, now);
  await addUsage(KIS_SOURCE, kis.calls, now, null).catch(() => undefined);
  const yahoo = kis.failed.length ? await fetchYahooQuotes(kis.failed) : { quotes: {}, errors: [] };
  return {
    quotes: { ...yahoo.quotes, ...kis.quotes },
    errors: yahoo.errors,
    kis: { got: Object.keys(kis.quotes).length, yahoo_fallback: kis.failed.length, calls: kis.calls, ...(kis.error ? { error: kis.error } : {}) },
  };
}

/** DART 가 "그날 공시 없음" 으로 주는 정상 응답. 오류가 아니다 */
const DART_NO_DATA = "013";
/** 요청 제한 초과 · 조회 가능 회사 수 초과. 더 부르면 안 된다 (batch/sources/dart_disclosures 와 같은 판정) */
// 일일 한도는 020 하나다. 021 은 조회 회사 수 초과(묶음 크기)라 한도가 아니다 (docs/infra.md 25.389)
const DART_LIMIT = new Set(["020"]);

/**
 * 보유 종목의 오늘 공시 (CLAUDE.md 장중 트리거 e).
 *
 * **한도를 센다** (docs/infra.md 25.105). 이 호출은 5분마다 보유 종목 수만큼 나가는데
 * 2026-09-22 까지 `api_usage` 에 한 번도 세지 않았다 — 배치만 세고 있었다.
 *
 * **이미 알린 종목은 다시 묻지 않는다.** 하루 한 번은 `alerts` 의 UNIQUE 가 막으므로,
 * 오늘 이미 `disclosure` 알림이 있는 종목을 또 불러도 **결과가 버려진다.** 장 한 번에
 * 종목당 84번(폐장 뒤 유예 30분 포함, 25.346·25.724)을 부르던 것이 첫 공시 이후로는 0번이 된다.
 */
async function disclosures(
  targets: Array<Target & { dart_corp_code: string | null }>,
  day: string,
  now: Date,
  alreadyAlerted: Set<number>,
  /** 전 거래일(없으면 오늘). 장 마감 뒤 공시를 놓치지 않으려고 여기부터 묻는다 (25.344) */
  since: string,
  /** 전 거래일에 알린 공시 알림의 `rcept_no` — 이미 알린 것을 다시 알리지 않는다 (25.344) */
  alertedBefore: Map<number, string>,
): Promise<{ hits: Hit[]; errors: string[] }> {
  const key = process.env.DART_API_KEY;
  // **키가 없으면 트리거 e 가 꺼진 것이다 — 조용히 넘어가지 않는다** (docs/infra.md 25.720, 감사). 배포 문서가 이 키를 Vercel 목록에
  // 적지 않아 문서대로 설정하면 보유 종목 공시 알림이 한 번도 안 나가는데, 기록은 errors 빈 배열(초록)이었다
  if (!key) {
    return targets.some((t) => t.dart_corp_code)
      ? { hits: [], errors: [`${DART_KEY_MISSING} — 보유 종목 공시 알림(트리거 e)이 꺼져 있습니다. Vercel 환경변수에 넣습니다`] }
      : { hits: [], errors: [] };
  }
  const 걸러낸 = targets.filter((t) => t.dart_corp_code && !alreadyAlerted.has(t.stock_id));
  // **호출마다 시작 종목을 돌린다** (25.600, 교차검증). 순서가 고정이라 DART 가 느린 날엔 마감 시간(DART_BUDGET_MS) 안에 앞쪽 몇 종목만
  // 매번 확인되고 뒤쪽은 그날 내내 확인되지 않았다. 5분 호출 번호로 돌려 모든 종목이 차례로 앞에 온다
  const 시작 = 걸러낸.length ? Math.floor(now.getTime() / 300_000) % 걸러낸.length : 0;
  const 후보 = [...걸러낸.slice(시작), ...걸러낸.slice(0, 시작)];
  if (후보.length === 0) return { hits: [], errors: [] };

  // **부르기 전에 한도를 본다.** 100% 에서 중단은 CLAUDE.md 비용 규칙이다
  const usage = await readUsage(DART_API_NAME, now, DART_DAILY_LIMIT).catch(() => null);
  if (usage && isBlocked(usage)) {
    return { hits: [], errors: [`DART 일일 한도(${usage.call_count}/${usage.limit_value}) — 공시 확인을 건너뜁니다`] };
  }

  const hits: Hit[] = [];
  const errors: string[] = [];
  let calls = 0;
  let blocked = false;
  const ymd = day.replaceAll("-", "");
  const 부터 = since.replaceAll("-", "");
  for (const t of 후보) {
    // **DART 에 쓸 수 있는 시간의 끝** (25.598, 교차검증). 종목마다 4초 제한을 둬도 보유가 8종목이면 32초라 30초 상한을 넘겼다 —
    // 남은 종목은 다음 호출(5분 뒤)이 본다 — 시작 종목을 돌리므로 차례로 모두 확인된다(공시 없는 종목은 매번 다시 후보다)
    if (Date.now() - now.getTime() > DART_BUDGET_MS) {
      errors.push(`시간이 모자라 공시 확인을 ${후보.length - calls}종목 남기고 멈췄습니다 — 다음 호출이 이어 봅니다`);
      break;
    }
    const url = `https://opendart.fss.or.kr/api/list.json?crtfc_key=${key}&corp_code=${t.dart_corp_code}&bgn_de=${부터}&end_de=${ymd}&page_count=10&sort=date&sort_mth=desc`;
    try {
      calls += 1;
      const body = (await (await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(DART_TIMEOUT_MS) })).json()) as {
        status?: string;
        message?: string;
        list?: Array<{ report_nm: string; rcept_no: string }>;
        total_count?: number | string;
      };
      const status = body.status ?? "";
      if (DART_LIMIT.has(status)) {
        // **막힌 것을 "공시 없음" 으로 보면 안 된다.** 예전에는 조용히 넘어가 구별이 안 됐다
        blocked = true;
        errors.push(`DART 요청 제한(${status}) — 남은 ${후보.length - calls}종목은 건너뜁니다`);
        break;
      }
      if (status !== "000" && status !== DART_NO_DATA) {
        errors.push(`DART ${status || "응답 이상"}: ${body.message ?? ""}`.trim());
        continue;
      }
      const 경계 = alertedBefore.get(t.stock_id) ?? null;
      const 받은 = body.list ?? [];
      const 새것 = freshDisclosures(받은, ymd, 경계);
      // 건수 (25.636, 감사): 받은 쪽(page_count=10) 밖에 더 있고 전 거래일 경계가 있으면, 못 받은 쪽에
      // 이미 알린 것이 섞였는지 모른다 — `total_count` 를 그대로 쓰면 이미 알린 것까지 "외 N건" 에 들어갔다.
      // 그때는 받은 것 가운데 새것만 세고 "이상" 을 붙인다. 경계가 없으면 전부 새것이라 total_count 가 맞다
      const 정확 = 경계 === null || !(Number(body.total_count) > 받은.length);
      const 그대로 = 정확 && 새것.length === 받은.length;
      const 고친것 = { list: 새것, total_count: 새것.length, atLeast: !정확 };
      const hit = status === "000" ? disclosureHit(t, 그대로 ? body : 고친것) : null;
      if (hit) hits.push(hit);
    } catch (error) {
      // 공시 확인 실패는 시세 알림을 막지 않는다. 다만 **조용히 넘어가지도 않는다**
      errors.push(`DART 호출 실패: ${error instanceof Error ? error.message : "알 수 없음"}`);
    }
  }
  await addUsage(DART_API_NAME, calls, now, DART_DAILY_LIMIT, { blocked }).catch(() => undefined);
  return { hits, errors };
}

export async function GET(request: Request) {
  if (!process.env.CRON_SECRET) {
    return NextResponse.json({ error: "CRON_SECRET 이 설정되지 않았습니다" }, { status: 503 });
  }
  if (!tokenMatches(request.headers.get("x-cron-secret"), process.env.CRON_SECRET)) {
    return NextResponse.json({ error: "토큰이 맞지 않습니다" }, { status: 401 });
  }
  const market = new URL(request.url).searchParams.get("market")?.toUpperCase() as Market | undefined;
  if (market !== "KR" && market !== "US") {
    return NextResponse.json({ error: "market=KR 또는 US" }, { status: 400 });
  }

  const now = new Date();
  // Turso 가 풀렸는지 10분에 한 번 본다 (docs/infra.md 25.12·25.47).
  //
  // **왜 여기에도 두나.** 이 감시는 `cron/health` 와 `cron/news` 에만 있었는데, 그 둘은
  // 2026-09-19 확인 시점까지 **호출 기록을 한 번도 남기지 않았다**(25.19, 원인 미확정).
  // 둘 다 안 불리면 "풀리면 알아서 돌아간다" 는 장치가 **통째로 안 돈다.** 반면 이 경로의
  // 호출 기록은 남아 있다 — 실제로 불린다는 증거가 있는 곳에 하나 더 얹는다.
  // 두 시장 장중(국내 09:00~15:55·미국 22:00~06:25 KST 무렵)과 조용시간 해제 호출에서만 불리므로 완전한 대체는 아니다. 세 곳이 서로를 받친다.
  // 장 밖 호출로 일찍 되돌아가는 경로보다 **앞에** 둔다 — 그래야 그때도 본다.
  if (now.getUTCMinutes() % 10 === 0) await watchTursoReturn().catch(() => undefined);
  const result: Record<string, unknown> = { market, at: now.toISOString() };
  try {
    const settings = await readSettings();
    const quiet = inQuietHours(settings.quiet_hours, now);
    result.quiet = quiet;
    if (!quiet) {
      result.flushed = await flushPending(now, settings.warnings).catch((e) => `발송 실패: ${e.message}`);
    }

    const sessions = rowsToObjects<Session>(
      await execute("SELECT date, open_utc, close_utc FROM market_sessions WHERE market = ? AND date BETWEEN ? AND ?", [
        market,
        // 직전 세션(감시 목록 신선도, 25.637)을 찾으려고 앞으로 넉넉히 — 추석·설 연휴를 덮는다
        new Date(now.getTime() - 8 * 86_400_000).toISOString().slice(0, 10),
        new Date(now.getTime() + 2 * 86_400_000).toISOString().slice(0, 10),
      ]),
    );
    const session = activeSession(sessions, now);
    if (!session) {
      // 장 밖 호출은 여기서 끝난다. 외부 크론이 잘못 불러도 시세를 받지 않는다
      // 창을 8일로 넓혔으니(25.637) "세션 정보 없음" 은 예전처럼 앞뒤 이틀 안에서 본다
      const 요즘 = new Date(now.getTime() - 2 * 86_400_000).toISOString().slice(0, 10);
      const skipped = sessions.some((x) => x.date >= 요즘) ? "장 밖" : "세션 정보 없음(일일 배치 확인)";
      await recordHeartbeat("intraday", market, now, heartbeatOutcome(`skipped:${skipped}`, result.flushed as number | string | undefined), {
        quiet,
        flushed: result.flushed ?? null,
        ...secretWeakDetail(process.env.CRON_SECRET),
      });
      return NextResponse.json({ ...result, skipped });
    }

    const thresholds = settings.alert_thresholds;
    const 목록 = rowsToObjects<Target & { reasons: string; dart_corp_code: string | null; built_at: string | null }>(
      await execute(
        `SELECT stock_id, yahoo_symbol, name, currency, reasons, buy_zone_low, buy_zone_high, target_price, stop_price,
                prev_close, avg_volume_20d, dart_corp_code, built_at
         FROM monitor_targets WHERE market = ?`,
        [market],
      ),
    );
    // **이번 장 목록이 아니면 신호 값은 쓰지 않는다** (docs/infra.md 25.534) — 어제 추천의 매수 구간 알림이 오늘의
    // 하루 1회 자리를 차지했다. 보유·관심 종목 감시는 그대로
    const 만든때 = 목록.map((t) => t.built_at).filter((v): v is string => Boolean(v)).sort().at(-1) ?? null;
    const 직전닫힘 = sessions.filter((x) => x.date < session.date).map((x) => x.close_utc).sort().at(-1) ?? null;
    const 이번장 = targetsFreshFor(만든때, session, 직전닫힘);
    const targets = 목록.map((t) => {
      const 풀린 = { ...t, reasons: JSON.parse(String(t.reasons)) as string[] };
      return 이번장 ? 풀린 : withoutSignalLevels(풀린);
    });
    // 상장폐지된 관심 종목은 감시하지 않는다 — 지난 장 시세만 와 판정 0건이 될 수 있다 (25.814, 감사). 유니버스에서 빠진(excluded) 종목은
    // 사용자가 일부러 걸어 둔 것이라 그대로 본다. 거래정지(상태는 active)는 이 조건으로 거르지 못한다 [확인필요: 운영에서 판정 0건으로 빨개지는지]
    const watch = rowsToObjects<Target & { target_buy_price: number | null }>(
      await execute(
        `SELECT w.stock_id, s.yahoo_symbol, COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.currency, w.target_buy_price,
                (SELECT close FROM prices WHERE stock_id = w.stock_id AND close IS NOT NULL ORDER BY date DESC LIMIT 1) AS prev_close,
                (SELECT CASE WHEN COUNT(*) = 20 AND MIN(date) >= date(MAX(date), '-45 days') THEN AVG(volume) END
                   FROM (SELECT volume, date FROM prices WHERE stock_id = w.stock_id AND volume > 0
                   ORDER BY date DESC LIMIT 20)) AS avg_volume_20d
         FROM watchlist w CROSS JOIN stocks s ON s.id = w.stock_id
         WHERE w.alert_enabled = 1 AND s.country = ? AND s.yahoo_symbol IS NOT NULL AND s.status <> 'delisted'`,
        [market],
      ),
    );

    // 20일 평균은 거래가 있은 최근 20일, 45일 안에서만 — 배치 monitor_targets 와 같은 식 (25.635, 감사)
    // 관심 종목만 있는 종목에도 기업행위 가드 — 최근 분할·병합이면 20일 평균 거래량을 쓰지 않는다 (25.595, 감사)
    const 대상ids = new Set(targets.map((t) => t.stock_id));
    const 관심만 = watch.filter((w) => !대상ids.has(w.stock_id));
    const 관심가드 = new Set<number>();
    if (관심만.length && market === "US") {
      const 기록 = rowsToObjects<{ value: string }>(
        await execute("SELECT value FROM settings WHERE key = 'us_split_detections'"),
      )[0]?.value;
      let 감지: Record<string, unknown> = {};
      try {
        감지 = JSON.parse(기록 ?? "{}") as Record<string, unknown>;
      } catch {
        감지 = {}; // 깨졌으면 모른다 — 배치 `recent_action` 도 모르면 가드하지 않는다
      }
      for (const w of 관심만) {
        if (recentActionUs(감지[String(w.stock_id)], now)) {
          w.avg_volume_20d = null;
          관심가드.add(w.stock_id);
        }
      }
    } else if (관심만.length) {
      // 한 요청으로 묶는다 — 종목마다 순차로 물으면 관심 30종목에 3~9초가 시세를 받기 전에 사라졌다 (25.598, 교차검증)
      const 결과 = await batch(관심만.map((w) => ({
        sql: "SELECT close, change_pct FROM prices WHERE stock_id = ? AND close IS NOT NULL AND close > 0 ORDER BY date DESC LIMIT ?",
        args: [w.stock_id, ACTION_GUARD_ROWS + 1],
      })));
      관심만.forEach((w, i) => {
        if (recentActionKr(rowsToObjects<{ close: number; change_pct: number | null }>(결과[i]))) {
          w.avg_volume_20d = null;
          관심가드.add(w.stock_id);
        }
      });
    }
    // 가드가 걸린 종목은 **관심 목표 매수가도** 쓰지 않는다 (25.598, 교차검증) — 분할 전에 적은 90,000원이 1:50 분할 뒤 2,000원과 견줘져
    // 사용자가 고칠 때까지 매일 "목표 매수가 도달" 이 나갔다. 관심만 있는 종목은 위 가드가 거래량을 비운 것으로, 보유·추천과 겹친 종목은
    // 배치 사유 `action_guard` 로 안다
    const 가드된 = new Set<number>([
      ...관심가드,
      ...targets.filter((t) => t.reasons.includes("action_guard")).map((t) => t.stock_id),
    ]);
    const byStock = new Map<number, { target: Target; watchBuy: number | null }>();
    for (const t of targets) byStock.set(t.stock_id, { target: t, watchBuy: null });
    for (const w of watch) {
      const existing = byStock.get(w.stock_id);
      if (existing) {
        existing.target.reasons = [...existing.target.reasons, "watch"];
        existing.watchBuy = 가드된.has(w.stock_id) ? null : w.target_buy_price;
      } else {
        byStock.set(w.stock_id, {
          target: { ...w, reasons: ["watch"], buy_zone_low: null, buy_zone_high: null, target_price: null, stop_price: null },
          watchBuy: 가드된.has(w.stock_id) ? null : w.target_buy_price,
        });
      }
    }

    const symbols = [...new Set([...byStock.values()].map((v) => v.target.yahoo_symbol))];
    const { quotes, errors, kis } = await fetchQuotes(symbols, market, now);
    // 야후 호출이 하나라도 실패했는지 — 그 호출에서 빠진 대상은 판정 못 한 것이다 (25.731)
    const quoteCallFailed = errors.length > 0;
    const hits: Hit[] = [];
    // 지난 장의 시세로는 판정하지 않는다 (docs/infra.md 25.196). 몇 종목이었는지는 기록에 남긴다
    const staleQuotes: string[] = [];
    // **시세를 아예 못 받은 종목도 남긴다** (docs/infra.md 25.345). 예전에는 조용히 건너뛰어 전체 개수만
    // 남았다 — 이전상장으로 `.KS` 가 `.KQ` 로 바뀐 보유 종목은 매일 판정에서 빠지는데 어느 종목인지 알 수 없었다
    const missingQuotes: string[] = [];
    const actionSuspects: string[] = [];
    const staleWatchPrices: string[] = [];
    for (const { target, watchBuy } of byStock.values()) {
      const quote = quotes[target.yahoo_symbol];
      if (!quote) {
        missingQuotes.push(target.yahoo_symbol);
        continue;
      }
      if (!isSessionQuote(quote, market, session.date)) {
        staleQuotes.push(`${quote.symbol}@${quote.time}`);
        continue;
      }
      // 기업행위 첫날로 보이면 가격 기준 알림을 그날 대지 않는다 — 걸린 종목은 호출 기록에 남긴다 (25.597)
      // 분할 전에 적은 듯한 목표 매수가는 쓰지 않는다 (25.600) — 가드 기간이 지나도, 추천과 겹친 종목에도
      const 쓸목표가 = watchPriceUsable(watchBuy, target.prev_close);
      if (watchBuy !== null && 쓸목표가 === null) staleWatchPrices.push(target.yahoo_symbol);
      const 판정 = evaluate(target, quote, thresholds, 쓸목표가);
      if (actionSuspect(market, quote.price, target.prev_close, quote.previous_close)) {
        actionSuspects.push(target.yahoo_symbol);
        const 뺄것 = dropOnActionSuspect(market);
        hits.push(...판정.filter((h) => !뺄것.has(h.trigger)));
      } else {
        hits.push(...판정);
      }
    }

    const day = localDate(market, now);
    const stamp = now.toISOString();
    // 하루 한 번은 DB 유니크가 보장한다. 이미 있으면 아무 일도 없다 — **조용시간에 "보내지 않음" 으로 둔 것만 조용시간 밖에서 되살린다** (25.797)
    const 넣기 = async (hs: typeof hits) =>
      hs.length
        ? await batch(
            hs.map((h) => ({
              sql: ALERT_UPSERT,
              // 조용시간에 "해제 뒤 보내기" 를 껐으면 알림 센터에만 남기고 보내지 않는다
              args: [h.stock_id, market, day, h.trigger, h.message, JSON.stringify(h.data), stamp,
                quiet && settings.quiet_hours?.deliver_on_release === false ? QUIET_SKIPPED : null],
            })),
          )
        : [];
    // **시세 알림을 DART 보다 먼저 저장한다** (docs/infra.md 25.595, 감사). 예전에는 보유 종목 수만큼 도는 순차 DART 호출 뒤에 한꺼번에 넣어,
    // DART 가 매달려 30초 상한에 걸리면 그 호출의 손절·목표·급등락 알림이 통째로 사라졌다
    const inserted = await 넣기(hits);
    if (market === "KR") {
      // 공시 알림은 보유 종목에만 나간다 — 그 종목들로 **색인을 타게** 묻는다 (25.595, 감사: `market, trade_date` 조건은 색인이 없어
      // alerts 표 전체를 호출마다 두 번 훑었다)
      const 보유ids = [...new Set(targets.map((t) => t.stock_id))];
      // 오늘 이미 공시 알림이 나간 종목은 DART 를 다시 부르지 않는다 (25.105)
      const alerted = new Set(
        rowsToObjects<{ stock_id: number }>(
          await execute(DISCLOSURE_ALERTS_FOR, [JSON.stringify(보유ids), day]),
        ).map((r) => Number(r.stock_id)),
      );
      // 전 거래일부터 묻는다 — 장 마감 뒤 공시가 다음 날에도 안 잡히던 틈 (docs/infra.md 25.344)
      const 전거래일 =
        rowsToObjects<{ d: string | null }>(
          await execute("SELECT MAX(date) AS d FROM market_sessions WHERE market = ? AND date < ?", [market, session.date]),
        )[0]?.d ?? session.date;
      const 전에_알림 = new Map<number, string>();
      for (const r of rowsToObjects<{ stock_id: number; data: string | null }>(
        await execute(DISCLOSURE_ALERTS_FOR, [JSON.stringify(보유ids), 전거래일]),
      )) {
        try {
          const no = (JSON.parse(r.data ?? "{}") as { rcept_no?: unknown }).rcept_no;
          if (typeof no === "string") 전에_알림.set(Number(r.stock_id), no);
        } catch {
          // 깨진 기록이면 모르는 것으로 둔다 — 그 종목의 전 거래일 공시가 다시 한 번 알려질 수 있다
        }
      }
      const dart = await disclosures(targets, session.date, now, alerted, 전거래일, 전에_알림);
      inserted.push(...(await 넣기(dart.hits)));
      errors.push(...dart.errors);
    }

    const newCount = inserted.reduce((sum, rs) => sum + rs.affectedRows, 0);

    let sent: number | string = 0;
    if (!quiet && newCount > 0) {
      sent = await flushPending(now, settings.warnings).catch((e) => `발송 실패: ${e.message}`);
    }

    // 판정한 수 = 대상 − 시세 없음 − 지난 장 시세 (25.731, 교차검증: 받은 시세 수로는 지난 장 시세만 받은 날도 초록이었다)
    const 판정수 = byStock.size - missingQuotes.length - staleQuotes.length;
    const 결과 = intradayOutcome(
      [result.flushed as number | string | undefined, sent], byStock.size, 판정수, errors,
      quoteCallFailed ? missingQuotes.length : 0,
      // 개장 직후 + 야후 실패 없음 + 받은 시세가 지난 장 것뿐 → 지연 시세로 설명된다 (25.735)
      withinOpenGrace(session.open_utc, now) && !quoteCallFailed && staleQuotes.length > 0,
    );
    await recordHeartbeat("intraday", market, now, 결과, {
      targets: byStock.size, quotes: Object.keys(quotes).length, new_alerts: newCount, sent, errors,
      // 국내 시세를 KIS 로 몇 개 받았고 몇 개를 야후로 물었나 (25.983)
      ...(kis ? { kis } : {}),
      ...(staleQuotes.length ? { stale_quotes: staleQuotes } : {}),
      ...(missingQuotes.length ? { missing_quotes: missingQuotes } : {}),
      // 기업행위 첫날로 보여 가격 알림을 건너뛴 종목 (25.597). 야후 전일 종가 동작을 확인하는 단서도 된다
      ...(actionSuspects.length ? { action_suspect: actionSuspects } : {}),
      ...(staleWatchPrices.length ? { stale_watch_price: staleWatchPrices } : {}),
      // 범위 밖 설정을 기본값으로 되돌렸으면 남긴다 (docs/infra.md 25.172)
      ...(settings.warnings.length ? { setting_warnings: settings.warnings } : {}),
      // **비밀이 짧으면 남긴다** (25.727, 감사) — 약하면 누구나 이 경로를 반복 호출해 DART 일일 한도(20,000)를 태울 수 있다.
      // 거부하지는 않는다(운영 중인 크론이 멈춘다). 바꾸는 일은 사용자 몫이라 handoff 에 적었다
      ...secretWeakDetail(process.env.CRON_SECRET),
    });
    return NextResponse.json({
      ...result,
      session: session.date,
      targets: byStock.size,
      quotes: Object.keys(quotes).length,
      hits: hits.length,
      new_alerts: newCount,
      sent,
      errors,
      missing_quotes: missingQuotes,
      setting_warnings: settings.warnings,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "실패";
    const quota = quotaReason(message);
    if (quota) {
      // 한도는 고장이 아니다. 500 을 쌓으면 cron-job.org 가 작업을 끌 수 있어 200 으로 답한다.
      // 호출 기록(heartbeat)도 쓰기라 남기지 않는다 (docs/infra.md 25.6). 멈췄다는 사실은 하루 한 번 알린다 (25.876)
      await notifyQuotaOnce("장중 감시", quota);
      return NextResponse.json({ skipped: quota });
    }
    // 새 표는 일일 배치(또는 migrate 워크플로)가 만든다. 그 전에 불리면 500 대신 할 일을 알려 준다 (2026-09-17 첫 Test run)
    if (/no such table/i.test(message)) {
      return NextResponse.json({
        ...result,
        skipped: "장중 알림 표가 아직 없습니다. Actions → 마이그레이션 적용 을 돌리거나 일일 배치를 기다리세요",
      });
    }
    await recordHeartbeat("intraday", market, now, "error", { error: message, ...secretWeakDetail(process.env.CRON_SECRET) });
    return NextResponse.json({ ...result, error: message }, { status: 500 });
  }
}
