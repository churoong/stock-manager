/**
 * 장중 모니터링 (docs/intraday.md, Step 14). 판정은 전부 순수 함수다. 네트워크·DB 는 경로(route)가 한다.
 *
 * 이 경로는 점수·신호·매매·보유를 바꾸지 않는다(CLAUDE.md "장중 알림은 판단 정보일 뿐").
 * 무엇을 쓰는지는 경로 머리말(`app/api/cron/intraday/route.ts`)이 적고 `__tests__/cronWrites.test.ts` 가
 * 지킨다. 2026-09-26 까지 여기도 "읽고 alerts 만 쓴다" 였다(docs/infra.md 25.195·25.196).
 */

import { MAX_SESSION_GAP_DAYS } from "@/lib/health";
import { localDate, userDateOf } from "@/lib/market";
import { WATCH_PRICE_MAX_RATIO } from "@/lib/watch";

export type Market = "KR" | "US";

/**
 * 감시 목록(`monitor_targets`)이 이보다 묵으면 **말한다** (docs/infra.md 25.162).
 *
 * 일일 배치가 거래일마다 다시 만든다. 잣대는 거래일 사이 최장 간격 11일
 * (`health.MAX_SESSION_GAP_DAYS`, 배치의 `metrics.MAX_SESSION_GAP_DAYS` 와 같은 값)이라
 * **정상 휴장으로는 안 걸린다.**
 *
 * **이 경로는 Actions 밖에서 돈다.** cron-job.org → Vercel 이라, 일일 배치가 멈춰도
 * 장중 감시는 계속 돈다 — 그래서 옛 목록·옛 구간으로 판정하는 일이 실제로 생긴다.
 */
export const MONITOR_STALE_DAYS = MAX_SESSION_GAP_DAYS;

const 시장이름 = (m: string) => (m === "KR" ? "국내" : "미국");

function 며칠전(builtAt: string | null | undefined, now: Date): number | null {
  if (!builtAt) return null;
  const t = Date.parse(builtAt);
  if (Number.isNaN(t)) return null;
  return Math.floor((now.getTime() - t) / 86_400_000);
}

/**
 * 감시 목록이 묵었으면 알림 묶음 머리에 붙일 한 줄. 아니면 `null`.
 *
 * **알림을 끄지는 않는다.** 보유 종목의 손절선은 사용자가 적은 평균단가에서 나오므로
 * 목록이 묵어도 대개 맞다. 틀릴 수 있는 것은 그 사이에 바뀐 것들이다 — 신호의 권장
 * 매수 구간, 판 종목, 20일 평균 거래량. 그래서 **말하고 보낸다**(25.136·25.161 과 같은 판단).
 */
export function staleTargetsNote(
  rows: Array<{ market: string; built_at: string | null }>,
  now: Date,
): string | null {
  const 묵은것 = rows
    .map((r) => ({ market: r.market, 나이: 며칠전(r.built_at, now), built_at: r.built_at }))
    .filter((r) => r.나이 !== null && r.나이 > MONITOR_STALE_DAYS)
    .sort((a, b) => a.market.localeCompare(b.market));
  if (묵은것.length === 0) return null;

  const 적기 = 묵은것
    .map((r) => `${시장이름(r.market)} ${userDateOf(r.built_at)}(${r.나이}일 전)`)
    .join(", ");
  return (
    `⚠ 감시 목록이 ${적기} 기준입니다. 일일 배치가 멈춘 동안` +
    " 옛 매수 구간·옛 보유로 판정합니다"
  );
}

export type TriggerType = "buy_zone" | "watch_price" | "target" | "stop" | "spike_up" | "spike_down" | "volume" | "disclosure";

/** 감시 목록이 이 장을 위해 만들어졌다고 보는 한계 — 장 시작 몇 시간 전까지 (docs/infra.md 25.534) */
export const TARGETS_FRESH_HOURS = 12;

/**
 * 감시 목록이 **이번 장** 것인가 (docs/infra.md 25.534, 감사 재현). 국내는 08:27 KST, 미국은 장 시작 약 1시간 전에 만든다.
 * 오늘 배치가 늦거나 실패하면 어제 목록이 남아, 어제 추천의 매수 구간으로 "권장 매수 구간 진입" 이 머리말 없이 나갔고
 * 그 알림이 그날의 "하루 1회" 자리를 차지해 배치가 끝난 뒤 오늘 신호의 알림이 막혔다.
 */
export function targetsFreshFor(builtAt: string | null, session: Session, prevCloseUtc: string | null = null): boolean {
  if (!builtAt) return false;
  const 만든 = new Date(builtAt).getTime();
  if (!Number.isFinite(만든)) return false;
  // **직전 세션이 닫힌 뒤에 만들었으면 이번 장 것이다** — 배치의 정의(monitor_targets.expected_signal_date, 25.547)와
  // 같다 (docs/infra.md 25.637, 감사). 12시간 창만 보면 국내 저녁(19시 KST) 재실행 목록이 이튿날 개장보다 14시간
  // 앞서 "묵음" 이 되고, 아침 배치까지 실패하면 올바른 신호 값이 하루 종일 꺼졌다. 직전 세션을 모를 때만 12시간 창
  const 닫힘 = prevCloseUtc ? new Date(prevCloseUtc).getTime() : NaN;
  if (Number.isFinite(닫힘)) return 만든 >= 닫힘;
  const 시작 = new Date(session.open_utc).getTime();
  return 만든 >= 시작 - TARGETS_FRESH_HOURS * 3600_000;
}

/**
 * 묵은 목록에서는 **신호에서 온 값**(매수 구간, 보유가 아닌 종목의 목표·손절)을 쓰지 않는다. 보유 목표·손절은 사용자가 적은
 * 평균단가에서 나와 그대로 둔다(25.161 과 같은 판단).
 */
export function withoutSignalLevels<T extends Target>(t: T): T {
  const 보유 = t.reasons.includes("holding");
  return {
    ...t,
    buy_zone_low: null,
    buy_zone_high: null,
    target_price: 보유 ? t.target_price : null,
    stop_price: 보유 ? t.stop_price : null,
  };
}

export interface Session {
  date: string;
  open_utc: string;
  close_utc: string;
}

export interface Target {
  stock_id: number;
  yahoo_symbol: string;
  name: string;
  currency: string;
  reasons: string[]; // holding / signal:mid / watch
  buy_zone_low: number | null;
  buy_zone_high: number | null;
  target_price: number | null;
  stop_price: number | null;
  prev_close: number | null;
  avg_volume_20d: number | null;
}

export interface Quote {
  symbol: string;
  price: number;
  time: string; // 시세 시각 ISO
  previous_close: number | null;
  day_high: number | null;
  day_low: number | null;
  volume: number | null;
  /** 어디서 받았나. 없으면 야후(`QUOTE_SOURCE`). 국내 KIS 는 `kis_openapi` (docs/infra.md 25.983) */
  source?: string;
}

export interface Thresholds {
  spike_pct: number;
  volume_multiple: number;
}

export interface Hit {
  stock_id: number;
  trigger: TriggerType;
  message: string;
  data: Record<string, unknown>;
}

/**
 * 지연 시세라 폐장 직후에도 마지막 값이 들어온다. 폐장 뒤 이만큼은 더 본다.
 * **30분** (docs/infra.md 25.724, 감사): 25분이면 크론이 15:55:00 보다 조금만 늦게 닿아도 마지막 호출이 장 밖이었고, 국내 지연(약 20~21분 실측,
 * infra 16장)과 겹쳐 15:20~15:30 종가 단일가 체결이 판정에서 빠졌다 — 그날 종가로 손절·목표를 건드리면 다음 날 저가가 새로 시작해 영영 놓친다
 */
export const CLOSE_GRACE_MINUTES = 30;

/** 지금 들어 있는 세션. 없으면 null (휴장일·장 밖). */
export function activeSession(sessions: Session[], now: Date): Session | null {
  const t = now.getTime();
  return (
    sessions.find(
      (s) => t >= Date.parse(s.open_utc) && t <= Date.parse(s.close_utc) + CLOSE_GRACE_MINUTES * 60_000,
    ) ?? null
  );
}

/**
 * 조용시간 해제 호출 시각(KST). cron-job.org 에 **이 시각 한 번**만 등록돼 있다(docs/intraday.md 4장).
 * 설정의 해제 시각이 이보다 늦으면 이 호출이 조용시간 안에 떨어져 아무것도 안 보낸다 (docs/infra.md 25.254)
 */
export const RELEASE_CALL_KST = "07:00";

/**
 * 해제 시각을 고르면 **밤새 쌓인 알림이 실제로 언제 나가는지** (docs/infra.md 25.254).
 * 해제 호출은 07:00 한 번뿐이라, 해제를 07:30·08:00 으로 두면 평일 09:00 국내 장 첫 호출까지(주말은 월요일) 밀린다.
 * 화면이 "해제 시각에 묶어 보낸다" 고만 하면 사용자는 08:00 에 받는 줄 안다.
 */
export function releaseNote(end: string): string {
  if (end <= RELEASE_CALL_KST) return `밤새 쌓인 알림은 ${RELEASE_CALL_KST} 해제 호출에 한꺼번에 나갑니다.`;
  return (
    `해제 호출은 ${RELEASE_CALL_KST} 한 번뿐이라, 해제를 ${end} 로 두면 밤새 쌓인 알림은` +
    " 평일 09:00 국내 장 첫 호출에 나갑니다(주말에는 월요일). 제때 받으려면 해제를 07:00 이하로 두세요."
  );
}

/** 한국 시각 HH:MM 이 조용시간 안인가. start > end 면 자정을 넘는 구간이다(예: 23:00~07:00). */
export function inQuietHours(
  quiet: { enabled: boolean; start: string; end: string } | null | undefined,
  now: Date,
): boolean {
  if (!quiet?.enabled) return false;
  const kst = new Date(now.getTime() + 9 * 3600_000);
  const hm = kst.toISOString().slice(11, 16);
  if (quiet.start === quiet.end) return false;
  return quiet.start < quiet.end ? hm >= quiet.start && hm < quiet.end : hm >= quiet.start || hm < quiet.end;
}

/** 장중 시세의 출처 이름 — 알림 행 `data.source` (25.815). 야후 spark 는 지연 시세다 */
export const QUOTE_SOURCE = "yahoo_spark";

/** 야후 spark 응답(한 번에 20종목까지, 2026-09-17 실측)에서 종목별 시세. */
export function parseSpark(payload: unknown): Record<string, Quote> {
  const out: Record<string, Quote> = {};
  const results = (payload as { spark?: { result?: unknown[] } })?.spark?.result;
  if (!Array.isArray(results)) return out;
  for (const item of results as Array<{ symbol?: string; response?: Array<{ meta?: Record<string, unknown> }> }>) {
    const meta = item.response?.[0]?.meta;
    const price = Number(meta?.regularMarketPrice);
    const time = Number(meta?.regularMarketTime);
    // **0 이하 가격은 값이 없는 것이다** (docs/infra.md 25.721, 감사 재현). 야후가 0 을 주면 "손절선 터치 (저가 0원, 현재 0원)" 와 급락이
    // 저장되고 그날 하루 1회 자리까지 차지했다 — 기업행위 가드(`actionSuspect`)도 가격 ≤0 은 의심하지 않았다
    if (!item.symbol || !meta || !Number.isFinite(price) || price <= 0 || !Number.isFinite(time)) continue;
    const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null);
    out[item.symbol] = {
      symbol: item.symbol,
      price,
      time: new Date(time * 1000).toISOString(),
      previous_close: num(meta.previousClose) ?? num(meta.chartPreviousClose),
      day_high: num(meta.regularMarketDayHigh),
      day_low: num(meta.regularMarketDayLow),
      // 거래량 0 은 "아직 체결 없음" 이라 값이다 — 가격과 달리 0 을 남긴다
      volume: typeof meta.regularMarketVolume === "number" && Number.isFinite(meta.regularMarketVolume) && meta.regularMarketVolume >= 0
        ? meta.regularMarketVolume
        : null,
    };
  }
  return out;
}

/**
 * 시세가 **이번 장의 것인가** (docs/infra.md 25.196).
 *
 * 야후는 거래가 잡히기 전까지 **지난 장의 시세**를 준다 — 가격·고가·저가·거래량·전일 종가가
 * 통째로 하루 밀린 값이다. 국내는 지연 시세라 개장 뒤 몇 번의 호출이 그렇고
 * `[확인필요: 국내 지연 폭]`, 거래정지 종목은 날마다 그렇다.
 *
 * 그 값으로 판정하면 **어제의 등락이 "오늘 급등" 으로** 나가고, 알림은 종목·트리거·날짜마다
 * 한 번이라 **그날 진짜 급등 알림이 막힌다.** 목표가·손절선도 어제 고가·저가로 "터치" 가 된다.
 *
 * 시세 시각을 그 시장 현지 날짜로 바꿔 장의 날짜와 대 본다. 다르면 판정하지 않는다 —
 * 다음 호출(5분 뒤)이 오늘 시세로 다시 본다.
 */
export function isSessionQuote(quote: Quote, market: Market, sessionDate: string): boolean {
  const at = new Date(quote.time);
  return !Number.isNaN(at.getTime()) && localDate(market, at) === sessionDate;
}

export const SPARK_MAX_SYMBOLS = 20;

export function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

function fmt(value: number, currency: string): string {
  return currency === "KRW"
    ? `${Math.round(value).toLocaleString("ko-KR")}원`
    : `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/**
 * 한 종목의 트리거 (docs/intraday.md 2장).
 *   buy_zone   현재가가 권장 매수 구간 안 (신호)
 *   watch_price 현재가가 관심 종목 목표 매수가 이하 (사용자 입력, 25.408)
 *   target     당일 고가가 목표가 이상 (보유·신호)
 *   stop       당일 저가가 손절가 이하 (보유·신호)
 *   spike_*    전일 종가 대비 ±N% (설정 alert_thresholds.spike_pct)
 *   volume     당일 누적 거래량이 20일 평균 거래량의 N배 초과 (설정 volume_multiple)
 */
export function evaluate(target: Target, quote: Quote, thresholds: Thresholds, watchBuyPrice: number | null = null): Hit[] {
  const hits: Hit[] = [];
  const c = target.currency;
  // 출처를 알림 행에 남긴다 — 모든 데이터 행에 source(CLAUDE.md). 받은 시각은 alerts.created_at (25.815, 감사)
  const base = { price: quote.price, quote_time: quote.time, symbol: quote.symbol, reasons: target.reasons, source: quote.source ?? QUOTE_SOURCE };
  const push = (trigger: TriggerType, message: string, extra: Record<string, unknown>) =>
    hits.push({ stock_id: target.stock_id, trigger, message: `${target.name}: ${message}`, data: { ...base, ...extra } });

  const inZone =
    target.buy_zone_low !== null && target.buy_zone_high !== null &&
    quote.price >= target.buy_zone_low && quote.price <= target.buy_zone_high;
  if (inZone) {
    push("buy_zone", `권장 매수 구간 진입 ${fmt(quote.price, c)} (구간 ${fmt(target.buy_zone_low!, c)}~${fmt(target.buy_zone_high!, c)})`,
      { buy_zone_low: target.buy_zone_low, buy_zone_high: target.buy_zone_high });
  }
  // **관심 목표가는 따로 센다** (docs/infra.md 25.408). 예전에는 같은 `buy_zone` 을 `else if` 로 썼다 —
  // 알림 표가 (종목, 트리거, 날짜)로 유일해서, 10시에 매수 구간 알림이 나가면 14시의 "관심 종목 목표 매수가 도달"
  // 은 삽입이 무시되어 **사용자가 직접 정한 목표가 알림을 그날 받지 못했다**(반대 순서도 같다)
  if (watchBuyPrice !== null && quote.price <= watchBuyPrice) {
    push("watch_price", `관심 종목 목표 매수가 도달 ${fmt(quote.price, c)} (목표 ${fmt(watchBuyPrice, c)} 이하)`,
      { watch_buy_price: watchBuyPrice });
  }

  const high = quote.day_high ?? quote.price;
  const low = quote.day_low ?? quote.price;
  // **부동소수 여유** (docs/infra.md 25.726, 감사 재현). 평균단가 70,000 × (1 − 0.07) 이 65,099.99999… 로 계산돼, 화면엔 "손절선 65,100원" 인데
  // 저가 65,100원에서 알림이 안 나갔다. 정확히 −10% 도 −9.999999999999998% 로 계산돼 급락이 안 났다. 경계는 "이상·이하" 가 규칙이다
  const 여유 = 1e-9;
  if (target.target_price !== null && high >= target.target_price * (1 - 여유)) {
    push("target", `목표가 ${fmt(target.target_price, c)} 터치 (고가 ${fmt(high, c)}, 현재 ${fmt(quote.price, c)})`,
      { target_price: target.target_price, day_high: high });
  }
  if (target.stop_price !== null && low <= target.stop_price * (1 + 여유)) {
    push("stop", `손절선 ${fmt(target.stop_price, c)} 터치 (저가 ${fmt(low, c)}, 현재 ${fmt(quote.price, c)})`,
      { stop_price: target.stop_price, day_low: low });
  }

  const prev = quote.previous_close ?? target.prev_close;
  if (prev && prev > 0) {
    const pct = Math.round((quote.price / prev - 1) * 100 * 1e9) / 1e9;
    if (pct >= thresholds.spike_pct) {
      push("spike_up", `전일 대비 +${pct.toFixed(1)}% 급등 (${fmt(prev, c)} → ${fmt(quote.price, c)})`,
        { previous_close: prev, change_pct: pct, threshold_pct: thresholds.spike_pct });
    } else if (pct <= -thresholds.spike_pct) {
      push("spike_down", `전일 대비 ${pct.toFixed(1)}% 급락 (${fmt(prev, c)} → ${fmt(quote.price, c)})`,
        { previous_close: prev, change_pct: pct, threshold_pct: thresholds.spike_pct });
    }
  }

  if (quote.volume !== null && target.avg_volume_20d && target.avg_volume_20d > 0) {
    const multiple = quote.volume / target.avg_volume_20d;
    if (multiple > thresholds.volume_multiple) {
      // 판정은 "N배 **초과**" 라 3.04배가 "3.0배" 로 보이면 문턱을 넘지 않은 것처럼 읽힌다 — 문턱과 달라질 때까지 자릿수를 늘린다 (25.797, 알림 감사 #3)
      let 자리 = 1;
      while (자리 < 4 && multiple.toFixed(자리) === thresholds.volume_multiple.toFixed(자리)) 자리++;
      push("volume", `거래량 20일 평균의 ${multiple.toFixed(자리)}배 (${Math.round(quote.volume).toLocaleString("ko-KR")}주)`,
        { volume: quote.volume, avg_volume_20d: target.avg_volume_20d, multiple, threshold: thresholds.volume_multiple });
    }
  }
  return hits;
}

/** 시장 현지 날짜. 알림의 "하루 한 번" 기준이다. */

/** 보낼 알림을 한 통으로. 조용시간이 끝난 뒤 밀린 것도 같은 모양으로 묶는다. */
export function bundleMessage(
  alerts: Array<{ market: string; message: string; created_at: string }>,
  now: Date,
  note: string | null = null,
): string {
  const kst = new Date(now.getTime() + 9 * 3600_000).toISOString().slice(11, 16);
  const lines = [`장중 알림 ${alerts.length}건 (${kst} KST, 지연 시세)`];
  // **한 번만 말한다.** 줄마다 붙이면 시끄러워 아무도 안 읽는다 (docs/infra.md 25.162)
  if (note) lines.push(note);
  const 오늘 = new Date(now.getTime() + 9 * 3600_000).toISOString().slice(0, 10);
  for (const a of alerts) {
    const 때 = new Date(Date.parse(a.created_at) + 9 * 3600_000).toISOString();
    // **다른 날 알림이면 날짜를 붙인다** (docs/infra.md 25.538, 감사 재현). 발송이 며칠 막혔다 풀리면 옛 손절 알림이
    // 시각만 찍혀 오늘 것처럼 보였다
    const at = 때.slice(0, 10) === 오늘 ? 때.slice(11, 16) : `${때.slice(5, 10)} ${때.slice(11, 16)}`;
    lines.push(`· [${a.market === "KR" ? "국내" : "미국"} ${at}] ${a.message}`);
  }
  // "자동 매매는 없다" 는 이 알림만의 말이고, 고지는 `sendTelegram` 이 붙인다 (25.115)
  lines.push("", "판단 정보일 뿐 자동 매매는 없습니다.");
  return lines.join("\n");
}

/**
 * 알림을 **한 통에 들어가는 묶음**으로 나눈다 (docs/infra.md 25.538, 감사 재현). 예전에는 50건을 한 글로 만들어
 * `sendTelegram` 이 여러 통으로 나눠 보냈는데, 둘째 통이 실패하면 전부 미발송으로 남아 다음 호출이 첫 통의 알림까지
 * **다시** 보냈다. 묶음마다 보내고 묶음마다 발송 표시를 하면 실패한 묶음만 다시 간다.
 * `여유` 는 `sendTelegram` 이 붙이는 고지 몫이다.
 */
export function alertGroups<T extends { market: string; message: string; created_at: string }>(
  alerts: T[], now: Date, note: string | null, limit: number, 여유 = 300,
): T[][] {
  const 묶음: T[][] = [];
  let 지금: T[] = [];
  for (const a of alerts) {
    const 시험 = [...지금, a];
    if (지금.length && bundleMessage(시험, now, 묶음.length ? null : note).length + 여유 > limit) {
      묶음.push(지금);
      지금 = [a];
    } else {
      지금 = 시험;
    }
  }
  if (지금.length) 묶음.push(지금);
  return 묶음;
}

/** 크론 비밀의 권장 최소 길이 — AUTH_SECRET 과 같은 잣대 (docs/infra.md 25.655·25.727) */
export const MIN_SECRET_LENGTH = 32;

/**
 * 비밀이 짧으면 호출 기록 detail 에 더할 표시 (25.727). **모든 호출 기록에 붙인다** (25.733, 교차검증) — 기록은 (job, market) 한 줄을
 * 통째로 덮으므로 장중 성공 호출에만 붙이면 뒤따르는 장 밖·오류 호출이 표시를 지웠다(미국 서머타임 05:35~06:25, 휴장일 내내).
 */
export function secretWeakDetail(secret: string | undefined): { secret_weak?: string } {
  return (secret ?? "").length < MIN_SECRET_LENGTH ? { secret_weak: `CRON_SECRET 이 ${MIN_SECRET_LENGTH}자 미만` } : {};
}

/** 헤더 토큰을 일정 시간에 비교한다(길이가 같을 때 글자마다 끝까지). */
export function tokenMatches(given: string | null, expected: string | undefined): boolean {
  if (!expected || !given || given.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= given.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

/**
 * DART 공시 목록 응답 하나를 알림 하나로 (docs/infra.md 25.288).
 *
 * 건수는 **응답의 `total_count`** 를 쓴다. 예전에는 `list.length` 를 셌는데 요청이 `page_count=10` 이라
 * 그날 공시가 15건이어도 "외 9건" 으로 잘렸고, 하루 한 번 규칙 때문에 그날 다시 고쳐지지 않았다.
 * `total_count` 가 없으면 [확인필요: DART list.json 명세] 받은 건수로 물러난다.
 */
export function disclosureHit(
  stock: { stock_id: number; name: string },
  body: { list?: Array<{ report_nm: string; rcept_no: string }>; total_count?: number | string; atLeast?: boolean },
): Hit | null {
  // **가장 큰 접수번호**를 고른다 (docs/infra.md 25.636, 감사). 이 값이 다음 거래일의 "이미 알림" 경계다
  // (freshDisclosures) — 응답 첫 행을 그대로 쓰면 같은 날 여러 건이 오름차순으로 올 때 작은 번호가 적혀
  // 이미 알린 공시가 이튿날 "새 공시" 로 다시 나갔다. 응답 정렬은 요청에 명시하지만 여기서도 믿지 않는다
  const first = [...(body.list ?? [])].sort((a, b) => (String(b.rcept_no) > String(a.rcept_no) ? 1 : -1))[0];
  if (!first) return null;
  const total = Number(body.total_count);
  const count = Number.isFinite(total) && total >= (body.list?.length ?? 1) ? total : (body.list?.length ?? 1);
  const 더 = count > 1 ? ` 외 ${count - 1}건${body.atLeast ? " 이상" : ""}` : "";
  return {
    stock_id: stock.stock_id,
    trigger: "disclosure",
    message: `${stock.name}: 새 공시 "${first.report_nm.trim()}"${더}`,
    // 출처 — 시세 알림과 같이 (25.817, 교차검증)
    data: { rcept_no: first.rcept_no, count, url: `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${first.rcept_no}`, source: "dart_list" },
  };
}

/**
 * 발송 결과를 호출 기록의 결과로 옮긴다 (docs/infra.md 25.338).
 *
 * 발송 실패는 문자열(`"발송 실패: …"`)로 잡혀 detail 에만 남고 결과는 `checked` 였다 — `/status` 와
 * [알림] 윗줄은 결과가 `error` 일 때만 빨갛게 칠하므로, 봇이 막히거나 대화 번호가 틀려 몇 시간 동안
 * 한 통도 안 나가도 **초록**이었다. 시세 판정은 해냈어도 **알리는 일을 못 했으면 실패다.**
 */
export function heartbeatOutcome(base: string, ...sends: Array<number | string | undefined | null>): string {
  return sends.some((s) => typeof s === "string") ? "error" : base;
}

/**
 * 장중 호출의 결과 (docs/infra.md 25.725, 감사). 발송 실패만 `error` 로 바꾸던 것(25.338)에 둘을 더한다 —
 * **대상 종목이 있는데 시세를 한 건도 못 받았으면**(야후 전부 429 등) 판정 자체를 못 한 것이고, **DART 키가 없으면**
 * 공시 트리거(e)가 꺼진 것이다(25.720). 둘 다 예전에는 `checked`(초록)라 몇 시간·며칠을 몰랐다.
 *
 * **셋째 인자는 받은 시세 수가 아니라 판정한 종목 수다** (25.731, 교차검증). 받은 시세가 모두 지난 장 것이면(야후가 멈춘 날)
 * 판정은 0건인데 받은 수로는 초록이었다. **넷째 인자는 야후 호출이 실패한 호출에서 시세가 빠진 대상 수**다 — 20종목씩 묶어
 * 부르므로 한 묶음만 429 여도 그 묶음의 보유 종목이 판정에서 빠지는데 초록이었다. 야후 실패 없이 빠진 종목(이전상장 등)은
 * 호출 기록의 `missing_quotes` 로 남기고 여기서는 빨갛게 하지 않는다 — 매 호출 빨개져 진짜 고장을 가린다.
 */
export function intradayOutcome(
  sends: Array<number | string | undefined | null>,
  targets: number,
  judged: number,
  errors: string[],
  missingOnQuoteFailure = 0,
  staleExpected = false,
): string {
  // 개장 직후엔 지연 시세가 지난 장 것이라 판정 0건이 정상이다(25.196) — 그때만 봐준다 (25.735, 교차검증)
  if (targets > 0 && judged === 0 && !staleExpected) return "error";
  if (missingOnQuoteFailure > 0) return "error";
  if (errors.some((e) => e.startsWith(DART_KEY_MISSING))) return "error";
  return heartbeatOutcome("checked", ...sends);
}

/**
 * 개장 뒤 이 시간 안에는 **받은 시세가 모두 지난 장 것이어도** 판정 0건을 오류로 보지 않는다 (docs/infra.md 25.735, 교차검증).
 * 25.731 이 판정 수로 보게 바꾸자 국내 09:00·09:05 호출이 지연 시세(25.196) 때문에 매일 빨개질 수 있었다.
 * 30분은 폐장 유예(`CLOSE_GRACE_MINUTES`)와 같은 잣대이고 국내 지연 폭(~20분으로 알려짐)보다 넉넉하다 `[확인필요: 실측 지연 폭]`.
 * 야후 호출 자체가 실패했거나 시세를 한 건도 못 받은 경우는 봐주지 않는다 — 지연으로 설명되지 않는다.
 */
export const OPEN_GRACE_MINUTES = 30;

export function withinOpenGrace(openUtc: string, now: Date): boolean {
  const 지남 = now.getTime() - Date.parse(openUtc);
  return 지남 >= 0 && 지남 < OPEN_GRACE_MINUTES * 60_000;
}

/** DART 키가 없을 때 장중 경로가 남기는 오류의 앞머리. 라우트와 판정이 같은 문자열을 쓴다 (25.731, 교차검증) */
export const DART_KEY_MISSING = "DART_API_KEY 없음";

/**
 * 전 거래일부터 물은 공시 목록에서 **아직 알리지 않은 것**만 남긴다 (docs/infra.md 25.344).
 *
 * 예전에는 오늘 하루만 물었다. 장중 경로는 15:55 에 끝나므로 **장 마감 뒤(또는 금요일 저녁) 접수된
 * 보유 종목 공시는 한 번도 알림이 안 나갔다** — 다음 날은 다음 날짜만 물었다. 그래서 전 거래일부터 묻는다.
 * 그러면 전 거래일 장중에 이미 알린 공시가 다시 잡히므로 걸러야 한다.
 *
 * `rcept_no` 는 접수일(8자리) + 일련번호라 날짜 안에서 뒤에 접수된 것이 더 크다 `[확인필요: 일련번호가
 * 접수 순서인지]`. 전 거래일에 알린 알림이 적어 둔 `rcept_no`(그때 목록의 가장 새 것)보다 **큰 것만** 새 것이다.
 * 전 거래일에 알림이 없었으면 그날 것도 전부 새 것이다(장 마감 뒤였거나 그때 DART 가 막혔다).
 */
export function freshDisclosures<T extends { rcept_no: string }>(
  list: T[],
  todayYmd: string,
  alertedRceptNo: string | null,
): T[] {
  return list.filter((d) => {
    const no = String(d.rcept_no ?? "");
    if (no.slice(0, 8) >= todayYmd) return true;
    return alertedRceptNo === null || no > alertedRceptNo;
  });
}

/** 종목 목록(JSON 배열)의 그날 공시 알림 — `alerts(stock_id, trigger_type, trade_date)` 유니크 색인을 탄다 (25.595) */
export const DISCLOSURE_ALERTS_FOR = `SELECT a.stock_id, a.data FROM json_each(?) j
JOIN alerts a ON a.stock_id = j.value AND a.trigger_type = 'disclosure' AND a.trade_date = ?`;

/**
 * 관심 종목만 있는 종목의 **기업행위 가드** (docs/infra.md 25.595, 감사). 보유·추천 종목은 배치(`monitor_targets.recent_action`)가
 * 20일 평균 거래량을 비우는데 관심 종목은 경로가 prices 에서 바로 읽어, 1:50 분할 뒤 약 6거래일 동안 "거래량 50배" 가 매일 나갔다
 * (docs/intraday.md 2장 "기업행위 뒤 20거래일은 거래량 트리거를 대지 않는다" 와 달랐다).
 * 국내 식은 배치 `adjust.factor_of`·`is_action` 과 같다 — 상수는 테스트가 파이썬과 맞춰 본다
 */
export const ADJUST_ACTION_TOLERANCE = 0.02;
export const ADJUST_MIN_FACTOR = 0.005;
export const ADJUST_MAX_FACTOR = 200.0;
export const ACTION_GUARD_ROWS = 20;

/** 최근 행(새것부터, `ACTION_GUARD_ROWS + 1` 개)에 기업행위가 있었나 — 국내 */
export function recentActionKr(rows: Array<{ close: number; change_pct: number | null }>): boolean {
  for (let i = 0; i + 1 < rows.length; i += 1) {
    const close = Number(rows[i].close);
    const prev = Number(rows[i + 1].close);
    const pct = rows[i].change_pct;
    if (pct === null || pct === undefined || !(prev > 0) || !(close > 0)) continue;
    const ratio = 1 + Number(pct) / 100;
    if (ratio <= 0) continue;
    const factor = close / prev / ratio;
    if (factor < ADJUST_MIN_FACTOR || factor > ADJUST_MAX_FACTOR) continue;
    if (Math.abs(factor - 1) > ADJUST_ACTION_TOLERANCE) return true;
  }
  return false;
}

/** 미국: 분할 감지 기록(`us_split_detections`, 25.535)이 가드 기간(거래일 20 ≈ 28일) 안인가 — 배치와 같은 판정 */
export function recentActionUs(detectedAt: unknown, now: Date): boolean {
  if (typeof detectedAt !== "string" || !detectedAt) return false;
  const 기간 = Math.floor((ACTION_GUARD_ROWS * 7) / 5) * 86_400_000;
  return detectedAt >= new Date(now.getTime() - 기간).toISOString();
}

/**
 * **기업행위 첫날** 의심 (docs/infra.md 25.597). 배치 가드(`recent_action`)는 DB 의 지난 행만 봐서, 분할·병합 뒤 거래가 다시 열린 첫날엔
 * 붙지 않는다 — 분할 전 평균단가의 손절가(93,000)가 1:5 분할 뒤 19,500 과 비교되어 거짓 "손절선 터치"·"목표 매수가 도달"·"거래량 N배"
 * (야후 전일 종가가 조정 안 됐으면 −80% "급락" 까지)가 나갔다.
 *
 * - 국내: 현재가 ÷ DB 전일 종가(원자료)가 가격제한폭 ±30% 밖(0.69·1.31, 호가 반올림 여유)이면 **정의상** 시세 움직임이 아니다
 * - 미국: 제한폭이 없어 넓게(0.6·1.7) 본다. 25.597 의 "야후·DB 전일 종가 5% 어긋남" 규칙은 DB 가 하루 묵은 날 어제 5% 넘게 움직인
 *   모든 종목의 손절을 지워 뺐다(25.600) — 3:2 분할(0.667) 첫날은 놓친다 [확인필요: 분할 첫날 야후 `previousClose` 가 조정된 값인지]
 * 모르면(DB 전일 종가 없음) 의심하지 않는다.
 */
export function actionSuspect(market: string, price: number, dbPrevClose: number | null, yahooPrevClose: number | null): boolean {
  if (!dbPrevClose || !(dbPrevClose > 0) || !(price > 0)) return false;
  const [lo, hi] = market === "KR" ? [0.69, 1.31] : [0.6, 1.7];
  const 밖 = (r: number) => r < lo || r > hi;
  if (!밖(price / dbPrevClose)) return false;
  // **DB 전일 종가가 묵었을 수 있다** (25.600, 교차검증). `prev_close` 는 prices 의 마지막 행일 뿐이라 배치가 늦거나 전일 행이 빠지면
  // 이틀치 누적(어제 −25% · 오늘 −10%)이 비율에 들어가 진짜 급락의 손절·급락 알림이 빠졌다. 야후 전일 종가가 있고 오늘 움직임이 그것 대비
  // 범위 안이며 야후·DB 전일 종가 차이도 하루치 움직임 범위 안이면 기업행위가 아니라 **DB 가 묵은 것**으로 본다.
  // 분할이면 야후가 조정했든(야후/DB 가 범위 밖) 안 했든(오늘/야후 가 범위 밖) 둘 중 하나는 범위 밖이다
  if (yahooPrevClose !== null && yahooPrevClose > 0 && !밖(price / yahooPrevClose) && !밖(yahooPrevClose / dbPrevClose)) return false;
  return true;
}

/**
 * 첫날 의심이면 뺄 트리거 (25.597). 미국은 진짜 폭락일 수 있어 급등락 알림은 남긴다 — 그 알림으로는 전해지게.
 * 국내는 제한폭 밖이라 급등락도 거짓이다
 */
export function dropOnActionSuspect(market: string): ReadonlySet<TriggerType> {
  return new Set<TriggerType>(
    market === "KR" ? ["buy_zone", "watch_price", "target", "stop", "spike_up", "spike_down", "volume"] : ["buy_zone", "watch_price", "target", "stop", "volume"],
  );
}

/** 전일 종가 대비 이 배수를 넘는 관심 목표 매수가는 분할 전 값으로 본다 (25.600·25.602). 저장 경고와 같은 값 — 정의처는 lib/watch (25.813) */
export { WATCH_PRICE_MAX_RATIO };

/**
 * 관심 목표 매수가가 **말이 되는가** (docs/infra.md 25.600, 교차검증). 전일 종가의 1.5배를 넘는 목표 매수가는 알림으로서 뜻이 없다 — 대개
 * 분할 전에 적은 값이다. 기업행위 가드(20거래일)가 풀린 뒤에도, 추천과 겹쳐 배치 가드가 안 붙은 종목에도 분할 전 90,000원이
 * 2,000원과 견줘져 매일 "목표 매수가 도달" 이 나갔다. 저장 때 25.360(10배)·25.584(종가 이상 경고)가 있지만 저장 뒤 분할은 못 본다
 */
export function watchPriceUsable(watchBuy: number | null, prevClose: number | null): number | null {
  if (watchBuy === null || !prevClose || !(prevClose > 0)) return watchBuy;
  // 1.5배 (25.602, 교차검증) — 두 배는 1:2 분할을 전혀 거르지 못했다. 1.5배도 **분할 전 가격의 75% 위에 적은 목표가만** 거른다
  // (1:2 분할 전 100,000 에 적은 70,000 은 분할 뒤 50,000 의 1.4배라 남는다 — 25.603 교차검증, 알고 둔다).
  // 대가: 목표가를 지난 뒤 −33% 넘게 더 빠진 종목의 **반복** 알림이 꺼진다(처음 지나는 날은 전일 종가가 높아 나간다)
  return watchBuy > prevClose * WATCH_PRICE_MAX_RATIO ? null : watchBuy;
}
