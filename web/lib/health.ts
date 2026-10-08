/**
 * 무응답 감시와 시스템 상태 (docs/health.md, Step 17).
 *
 * **판단은 전부 순수 함수다.** 시각·세션·마지막 성공을 인자로 받아 "알려야 하는가" 만
 * 돌려준다. DB 도 fetch 도 모르기 때문에 손으로 만든 값으로 전부 테스트할 수 있다.
 *
 * 왜 이 감시가 필요한가: 배치는 **실패**를 스스로 알리지만 **안 도는 것**은 알릴 수 없다.
 * Actions 예약이 밀리거나 워크플로가 꺼지면 아무 일도 일어나지 않는다 (docs/health.md 0장).
 */

import { INTRADAY_ENABLED, INTRADAY_OFF_NOTE } from "@/lib/intradaySwitch";
import { localDate, userDateOf, userTimeOf } from "@/lib/market";

export type Market = "KR" | "US";

// 현지 날짜는 web/lib/market.ts 가 단일 정의처다 (docs/infra.md 25.59). 여기 사본이 있었다
export { localDate };

/**
 * 감시 대상. 마감은 **그 시장 개장 시각에서 잰 분**이다.
 *
 * 절대 시각을 적지 않는 이유: 서머타임과 임시공휴일이 저절로 맞고, 그날 세션이 없으면
 * 휴장일이라 아무것도 기대하지 않게 된다. 숫자의 근거는 docs/health.md 2장 표에 있다.
 */
export interface Watch {
  job: string;
  market: Market;
  label: string;
  /** 개장 기준 오프셋(분). 음수면 개장 전 */
  dueOffsetMinutes: number;
  /** 알림에 적는 확인 방법 */
  where: string;
  /** 예약 시각 설명. 정의처는 워크플로의 cron 이고 여기는 사람이 읽는 글이다 (테스트가 둘을 대조한다) */
  schedule: string;
  /** 같은 날 자동 재시도가 있으면 무응답 알림에 적는다 — 손으로 확인하러 가기 전에 기다릴지 알 수 있게 (25.917) */
  retryNote?: string;
}

/**
 * "오늘 것" 으로 칠 성공의 창. 개장 시각에서 이만큼 앞까지 거슬러 본다.
 *
 * **`batch_runs.trade_date` 로 판단하면 안 된다.** 장 시작 전에 도는 배치라 그 값은
 * *직전* 거래일이다(batch/core/calendar.decide: "다루는 데이터는 직전 거래일"). 오늘
 * 날짜와 비교하면 성공한 날에도 매일 무응답 알림이 간다.
 *
 * 그래서 **끝난 시각**으로 본다. 12시간이면 국내(개장 33분 전 시작)와 미국(1시간 전),
 * 국내 감성(개장 80분 전)을 모두 덮으면서 전날 실행(약 24시간 전)과는 겹치지 않는다.
 */
export const SUCCESS_WINDOW_HOURS = 12;

export const WATCHES: Watch[] = [
  { job: "daily_kr", market: "KR", label: "국내 일일 배치", dueOffsetMinutes: 20, where: "GitHub Actions → 일일 배치 (국내)", schedule: "매 거래일 08:27 KST (cron-job.org → daily-kr.yml), 실패·건너뜀이면 09:35 KST 예비 실행", retryNote: "09:35 KST 에 예비 실행이 한 번 더 시도합니다 — 그 뒤에도 없으면 손으로 확인하세요" },
  { job: "daily_us", market: "US", label: "미국 일일 배치", dueOffsetMinutes: 0, where: "GitHub Actions → 일일 배치 (미국)", schedule: "미국 정규장 1시간 전 (08:27 ET, daily-us.yml)" },
  { job: "sentiment", market: "KR", label: "국내 뉴스 감성 채점", dueOffsetMinutes: -30, where: "GitHub Actions → 국내 뉴스 감성", schedule: "매 거래일 07:40 KST (sentiment-kr.yml)" },
];

export interface Session {
  market: string;
  date: string;
  open_utc: string;
}

/** batch_runs 에서 뽑은 그 대상의 마지막 성공 */
export interface LastSuccess {
  job_name: string;
  market: string | null;
  trade_date: string | null;
  finished_at: string | null;
  status?: string;
}

export interface Due {
  job: string;
  market: Market;
  /** `calendar-low` 는 **아직 안 끊겼지만 곧 끊긴다**는 뜻이다 (docs/infra.md 25.104) */
  kind: "missing" | "calendar" | "calendar-low";
  localDate: string;
  message: string;
  data: Record<string, unknown>;
}

// ----------------------------------------------------------------------
// 판단
// ----------------------------------------------------------------------

/**
 * 오늘 칸 뒤에 붙는 말. null 은 달력이 오늘까지 오지 않았거나 못 읽어 모르는 것 — 휴장이 아니다 (25.552).
 * D1 운영 중 쉬는 미국은 달력도 마르므로 "배치가 멈췄는지 확인" 이 아니라 쉰다고 말한다 (25.555, 교차검증)
 */
export function dayNote(tradingDay: boolean | null, paused = false): string {
  if (paused) return " (D1 운영 중 쉼)";
  if (tradingDay === null) return " (달력 없음 — 배치가 멈췄거나 달력을 못 읽음)";
  return tradingDay ? "" : " (휴장)";
}

/** 그 시장의 오늘(현지) 세션. 없으면 휴장일이거나 달력이 끊긴 것이다 */
export function sessionOn(sessions: Session[], market: Market, localDate: string): Session | null {
  return sessions.find((s) => s.market === market && s.date === localDate) ?? null;
}


/** 사람이 읽는 경과 시간. "얼마나 오래 멈췄나" 가 판단에 필요하다 */
export function sinceText(from: string | null, now: Date): string {
  if (!from) return "기록 없음";
  const ms = now.getTime() - Date.parse(from);
  if (!Number.isFinite(ms)) return "기록 없음";
  const hours = Math.floor(ms / 3_600_000);
  if (hours < 1) return "1시간 이내";
  if (hours < 48) return `${hours}시간 전`;
  return `${Math.floor(hours / 24)}일 전`;
}

/**
 * **분 단위로 도는 것**의 경과 시간 (docs/infra.md 25.141 덧).
 *
 * `sinceText` 는 한 시간 미만을 전부 "1시간 이내" 로 뭉친다. 하루에 한 번 도는 배치에는
 * 맞는 굵기인데, **1분 크론**에 그 말을 붙이면 20분을 거른 줄이 "1시간 이내" 라고 적힌
 * 채 노랗게 뜬다 — 색과 글자가 서로 딴말을 한다. 사람은 글자를 먼저 읽는다.
 */
export function sinceMinutesText(from: string | null, now: Date): string {
  if (!from) return "기록 없음";
  const ms = now.getTime() - Date.parse(from);
  if (!Number.isFinite(ms)) return "기록 없음";
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "방금";
  if (minutes < 120) return `${minutes}분 전`;
  return sinceText(from, now);
}

/** 이 세션의 실행으로 칠 수 있는 가장 이른 시각 */
export function successWindowFrom(session: Session): string {
  return new Date(Date.parse(session.open_utc) - SUCCESS_WINDOW_HOURS * 3_600_000).toISOString();
}

/**
 * 그 대상의 마지막 성공. **시장까지 맞춘다** (docs/infra.md 25.128).
 *
 * `sentiment` 는 이름 하나로 두 시장에서 돈다(`daily_kr`·`daily_us` 가 각각 부른다).
 * 시장을 안 보면 **미국 감성의 성공이 국내 감성 감시를 잠재운다** — 국내가 몇 주 죽어
 * 있어도 조용하다. 감시가 자기 눈을 가리는 셈이다.
 *
 * **시장이 비어 있는(NULL) 기록은 대안으로만 쓴다.** 시장을 안 남기는 작업
 * (`portfolio`·`sell_flags` 처럼 나라가 섞인 것)이 나중에 감시 대상이 될 수 있는데,
 * 그때 딱 맞는 기록이 없다고 매일 거짓 알림을 보내면 감시 자체를 못 믿게 된다.
 */
export function findLastSuccess(rows: LastSuccess[], watch: Pick<Watch, "job" | "market">): LastSuccess | null {
  const mine = rows.filter((r) => r.job_name === watch.job);
  return mine.find((r) => r.market === watch.market) ?? mine.find((r) => r.market === null) ?? null;
}

/**
 * 그 성공이 **오늘 세션을 위한 실행**인가.
 *
 * trade_date 가 아니라 finished_at 으로 본다. 이유는 SUCCESS_WINDOW_HOURS 주석에 있다.
 */
export function countsForToday(last: LastSuccess | null, session: Session): boolean {
  if (!last?.finished_at) return false;
  if (last.finished_at < successWindowFrom(session)) return false;
  // **그 시장의 오늘 날짜에 끝났어야 한다** (docs/infra.md 25.376). 12시간 창의 시작은 국내 개장 09:00 − 12h =
  // **전날 21:00 KST** 라, 전날 밤 22:00 에 /status 에서 손으로 다시 돌린 성공이 "오늘 성공" 으로 세졌다 —
  // 다음 날 08:27 예약이 안 불려도 알림 0건이었다. 예약 배치(국내 07:40·08:27 KST, 미국 08:27 ET)는 모두 현지 자정 뒤다
  const t = Date.parse(last.finished_at);
  return Number.isNaN(t) ? true : localDate(session.market as "KR" | "US", new Date(t)) === session.date;
}

/**
 * 거래일 달력이 이만큼 남으면 **미리** 알린다 (docs/infra.md 25.104).
 *
 * `market_sessions` 는 일일 배치(`jobs/monitor_targets`)가 `SESSION_DAYS = 14` 일치를
 * 채운다. 배치가 멈추면 하루에 한 거래일씩 마르고, **다 마르는 순간 장중 감시가
 * 조용히 멈춘다**(`/api/cron/intraday` 가 "세션 정보 없음" 으로 끝난다).
 *
 * 2026-09-22 까지 경고는 **다 마른 뒤에야** 울렸다(`future.length > 0` 이면 넘어갔다) —
 * 열흘치 여유가 있는데 **하루도 쓰지 않았다.** 25.0 의 "잃고서 초록으로 알린다" 다.
 *
 * 왜 3 인가 — **실측**이다. `exchange_calendars` 로 2024-01-01~2026-12-31 의 **모든 날**을
 * 시작점으로 잡아 14일 창이 덮는 거래일을 세면 국내 **5~11일**, 미국 **8~11일**이다
 * (국내 최솟값 5는 2025-09-27 추석 연휴, 미국 8은 2024-12-21 연말).
 *
 * 즉 배치가 정상으로 도는 날 아침의 잔량은 **최악에도 5**다. 3 이면 정상 운영에서는
 * 절대 안 울린다. 울렸을 때는 아직 세 거래일이 남아 있으므로 사람이 손쓸 틈이 있다.
 * (연휴 직전이라 5로 시작했다면 두 거래일 만에 울린다 — 그래도 남은 사흘은 그대로다.)
 */
export const CALENDAR_LOW_SESSIONS = 3;

/**
 * 지금 알려야 하는 것들.
 *
 * 규칙은 셋뿐이다 (docs/health.md 2·3장).
 *   1. 그 시장 오늘 세션이 없으면 휴장일이다 → 아무것도 기대하지 않는다
 *   2. 마감(개장 ± 오프셋)이 지났는데 오늘 날짜의 성공 기록이 없으면 알린다
 *   3. 오늘 이후 세션이 `CALENDAR_LOW_SESSIONS` 이하로 남으면 알린다 → 다 끊기기 **전에** 알린다
 *
 * 이미 보낸 것(existing)은 빼고 돌려준다. 실제 중복 차단은 DB UNIQUE 가 한다.
 */
export function dueAlerts(
  now: Date,
  sessions: Session[],
  lastSuccess: LastSuccess[],
  existing: Array<{ job: string; market: string; local_date: string; kind: string }>,
  watches: Watch[] = WATCHES,
  /** 지켜볼 시장. D1 임시 운영 중에는 미국을 쉬므로 국내만 본다 (docs/infra.md 25.14) */
  markets: Market[] = ["KR", "US"],
): Due[] {
  const already = new Set(existing.map((e) => `${e.job}|${e.market}|${e.local_date}|${e.kind}`));
  const out: Due[] = [];

  for (const watch of watches) {
    if (!markets.includes(watch.market)) continue;
    const today = localDate(watch.market, now);
    const session = sessionOn(sessions, watch.market, today);
    if (!session) continue; // 1. 휴장일

    const due = new Date(Date.parse(session.open_utc) + watch.dueOffsetMinutes * 60_000);
    if (now < due) continue; // 아직 기다릴 시각이다

    const last = findLastSuccess(lastSuccess, watch);
    if (countsForToday(last, session)) continue; // 오늘 것은 이미 성공했다

    const key = `${watch.job}|${watch.market}|${today}|missing`;
    if (already.has(key)) continue;

    out.push({
      job: watch.job,
      market: watch.market,
      kind: "missing",
      localDate: today,
      message:
        // **사람이 읽는 시각은 한국 시각이다** (docs/infra.md 25.297). 예전에는 `2026-09-18T00:20:00.000Z` 를 그대로 적어
        // 폰으로 받은 사용자가 9시를 넘었는지 머릿속으로 9시간을 더해야 했다. 기계용 UTC 는 data 에 남는다
        `[무응답] ${watch.label}가 ${userTimeOf(due.toISOString())} (한국 시각) 까지 성공 기록이 없습니다.\n` +
        `마지막 성공: ${last?.finished_at ? `${userTimeOf(last.finished_at)} (한국 시각)` : "없음"}` +
        ` (${sinceText(last?.finished_at ?? null, now)})\n` +
        `확인: ${watch.where}` +
        (watch.retryNote ? `\n${watch.retryNote}` : ""),
      data: {
        due_utc: due.toISOString(),
        success_window_from: successWindowFrom(session),
        due_offset_minutes: watch.dueOffsetMinutes,
        session_open_utc: session.open_utc,
        last_success_at: last?.finished_at ?? null,
        last_success_trade_date: last?.trade_date ?? null,
      },
    });
  }

  // 3. 달력이 끊겼나 — 그리고 **끊기기 전에** 알린다 (시장별로 한 번)
  for (const market of markets) {
    const today = localDate(market, now);
    const future = sessions.filter((s) => s.market === market && s.date >= today);
    if (future.length > CALENDAR_LOW_SESSIONS) continue;

    const 비었나 = future.length === 0;
    const key = `calendar|${market}|${today}|${비었나 ? "calendar" : "calendar-low"}`;
    if (already.has(key)) continue;
    const 나라 = market === "KR" ? "국내" : "미국";
    out.push({
      job: "calendar",
      market,
      kind: 비었나 ? "calendar" : "calendar-low",
      localDate: today,
      message: 비었나
        ? `[무응답] ${나라} 거래일 달력(market_sessions)이 ${today} 이후로 비어 있습니다.\n` +
          "**장중 감시가 지금 멈춰 있습니다** — 세션을 못 찾아 시세를 받지 않습니다.\n" +
          "일일 배치가 달력을 채우므로, 오래 멈춘 것일 수 있습니다.\n" +
          "확인: GitHub Actions → 일일 배치"
        : `[주의] ${나라} 거래일 달력이 **${future.length}거래일** 남았습니다 (마지막 ${future[future.length - 1].date}).\n` +
          "다 떨어지면 **장중 감시가 조용히 멈춥니다.** 일일 배치가 한 번만 돌면 다시 채워집니다.\n" +
          "확인: GitHub Actions → 일일 배치",
      data: { checked_local_date: today, future_sessions: future.length },
    });
  }
  return out;
}

// ----------------------------------------------------------------------
// 질의
// ----------------------------------------------------------------------

/** 오늘 전후의 세션. 휴장일 판정과 달력 고갈 판정에 함께 쓴다 */
// 시장을 적어 주키(market, date)로 찾게 한다 — 날짜만이면 표 전체를 훑었다 (docs/infra.md 25.862, 감사)
export const SESSIONS_AROUND = `SELECT market, date, open_utc FROM market_sessions WHERE market IN ('KR', 'US') AND date >= ? ORDER BY market, date LIMIT 200`;

/**
 * 미국 수정주가 **재수집을 기다리는** 종목 (docs/infra.md 25.165).
 *
 * 일일 배치가 어긋남을 찾아 대기열에 적고, 토요일 재수집이 비운다. 그 사이 그 종목의
 * 옛 `adj_close` 는 **지금 기준과 어긋나 있고**, 모멘텀·성과지표·백테스트가 그 값을 읽는다.
 * 그런데 감지 사실은 `log.info` 에만 남아 있었다 — 우리는 Actions 로그를 못 읽는다(25.17).
 */
export const ADJUST_QUEUE = `SELECT COUNT(*) AS n, MIN(detected_at) AS oldest FROM adjust_refresh_queue WHERE done_at IS NULL`;

/** 배치의 `refresh_us_adjusted.MAX_PENDING_DAYS` 와 같은 값이어야 한다 (주 1회 × 2번) */
export const ADJUST_QUEUE_STALE_DAYS = 15;

export interface AdjustQueue {
  n: number;
  oldest: string | null;
}

/** 대기열 한 줄. 비어 있으면 `null` — **빈 것이 정상**이라 아무 말도 안 한다 */
export function adjustQueueNote(q: AdjustQueue | null, now: Date): { text: string; late: boolean } | null {
  if (!q || !q.n) return null;
  const 나이 = q.oldest ? Math.floor((now.getTime() - Date.parse(q.oldest)) / 86_400_000) : null;
  const 늦음 = 나이 !== null && 나이 > ADJUST_QUEUE_STALE_DAYS;
  const 꼬리 = 나이 === null ? "" : ` · 가장 오래된 감지 ${userDateOf(q.oldest)} (${나이}일 전)`;
  return {
    text:
      `미국 수정주가 재수집 대기 ${q.n}종목${꼬리}` +
      (늦음 ? " — 그 종목의 옛 수정종가가 지금 기준과 어긋나 있습니다" : ""),
    late: 늦음,
  };
}

/**
 * 감시 대상별 마지막 "돌았다" 기록.
 *
 * `partial` 도 센다. 이 감시가 묻는 것은 **아무 일도 없었는가** 이지 완벽했는가가 아니다.
 * 운영에서 국내 일일 배치가 경고를 안고 `partial` 로 끝나면서 리포트는 정상 발송한 날이
 * 있었다(2026-09-17). 그날 "무응답" 을 보내면 거짓 알림이다. 실패(`failed`)는 배치가
 * 스스로 알리고, `dryrun`(수동 시험)·`skipped`(휴장·지각)는 리포트를 내지 않으므로 세지 않는다.
 */
export const RAN_STATUSES = ["success", "partial"] as const;

/**
 * 작업마다 마지막으로 "돌았다" 기록 하나.
 *
 * **상관 서브쿼리를 쓰지 않는다.** 예전에는 행마다 batch_runs 를 다시 훑어(job_name 으로 걸러 MAX)
 * 기록이 쌓일수록 읽는 행이 제곱으로 늘었다. 이 질의는 무응답 감시가 **매시간** 부른다.
 * 2026-09-18 읽기 한도 사고 뒤에 한 번 훑는 형태로 바꿨다 (docs/infra.md 24.4).
 *
 * MAX() 와 함께 적은 열은 **그 최댓값이 있는 행의 값**이다 — SQLite 가 보장하는 동작이며
 * (bare columns in an aggregate query) libSQL 도 같다. 다른 DB 로 옮기면 이 질의를 다시 써야 한다.
 *
 * **시장까지 묶는다** (2026-09-22, docs/infra.md 25.128). `job_name` 으로만 묶으면 한 이름이
 * 두 시장에서 도는 작업(`sentiment` — `daily_kr`·`daily_us` 가 각각 부른다)의 성공이
 * **하나로 뭉개진다.** 미국 감성이 돌면 국내 감성이 몇 주 죽어 있어도 무응답 감시가 조용하다.
 * 25.111·25.112 와 같은 모양이다 — 표 전체에서 MAX 를 잡아 한쪽 시장이 사라진다.
 */
export const LAST_SUCCESS = `SELECT job_name, market, trade_date, status, MAX(finished_at) AS finished_at
FROM batch_runs WHERE status IN (${RAN_STATUSES.map((s) => `'${s}'`).join(", ")}) AND finished_at IS NOT NULL
GROUP BY job_name, market`;

export const TODAY_HEALTH_ALERTS = `SELECT job, market, local_date, kind FROM health_alerts WHERE local_date >= ?`;

export const HEALTH_ALERT_INSERT = `INSERT INTO health_alerts (job, market, local_date, kind, message, data, created_at, sent_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (job, market, local_date, kind) DO NOTHING`;

export const PENDING_HEALTH_ALERTS = `SELECT id, message FROM health_alerts WHERE sent_at IS NULL ORDER BY created_at LIMIT 20`;

export const RECENT_BATCH_RUNS = `SELECT id, job_name, market, trade_date, trigger_source, scheduled_for, started_at,
  finished_at, status, delay_seconds, step_log, error_text
FROM batch_runs ORDER BY id DESC LIMIT 25`;
// `id` 순서 = 시작 순서다(`start_batch_run` 이 시작할 때 넣는다). `started_at` 으로 정렬하면 색인이 없어 표 전체를 정렬했다 —
// 1년이면 약 1.5만 행을 상태 화면을 열 때마다 읽었다 (docs/infra.md 25.862, 감사)

/**
 * API 한도 사용량. 일 단위(window_start='2026-09-17')와 월 단위('2026-09')가 섞여 있다.
 *
 * 날짜 하나로 자르면 월 단위 행이 빠진다 — '2026-09' 는 '2026-09-16' 보다 **작다**
 * (짧은 쪽이 앞선다). 그래서 종류별로 나눠 받는다. Turso 쓰기 예산이 월 단위라
 * 이걸 놓치면 정작 봐야 할 게이지가 화면에 안 뜬다 (docs/infra.md 23절).
 */
export const API_USAGE = `SELECT api_name, window_type, window_start, call_count, limit_value, warn_at_pct, state, last_call_at
FROM api_usage
WHERE (window_type = 'day' AND window_start >= ?) OR (window_type = 'month' AND window_start >= ?)
ORDER BY CASE window_type WHEN 'month' THEN 0 ELSE 1 END, api_name`;

/**
 * 월 단위 창의 기준값. 이번 달과 지난달까지 본다.
 * **1일로 옮긴 뒤 한 달을 뺀다** (docs/infra.md 25.593, 감사) — 10/31 에서 달만 빼면 "9/31" → 10/1 로 넘쳐 지난달을 빠뜨렸다
 */
export function monthWindow(now: Date): string {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1));
  return d.toISOString().slice(0, 7);
}

/**
 * 한도 게이지에 올릴 행을 고른다 (docs/infra.md 25.593, 감사). 예전에는 달력 판정용 36시간 창을 그대로 써 하루 창이 이틀~사흘 치,
 * 월 창은 지난달까지 나왔고, 9/26 에 가득 찬 D1 쓰기가 9/28 화면에 "D1 쓴 행 (오늘) · 100%" 빨간 막대로 떴다 — 거짓 경보가
 * 사람을 멈추게 한다. 이제 (API, 창 종류)마다 **가장 새 창 하나**만 두고, 지금 창(오늘 UTC·이번 달)이 아니면 `current: false` 로 표시한다.
 * 한도 셈의 하루 경계는 UTC 다(25.322)
 */
/** 횟수가 아니라 수위(지금 얼마나 찼나)를 적는 행 — 창 날짜가 지나도 지금 값이다 (25.594) */
export const LEVEL_GAUGES = new Set(["d1_db_size"]);
/** 수위 값을 지금 것으로 쳐 주는 나이. 주말·연휴에 일일 배치가 안 돌아도 넘지 않게, 국내 최장 연휴(11일)보다 길게 (25.596) */
export const LEVEL_GAUGE_MAX_AGE_DAYS = 14;

export function pickUsageRows<T extends { api_name: string; window_type: string; window_start: string }>(
  rows: T[],
  now: Date,
): Array<T & { current: boolean }> {
  const 가장새 = new Map<string, T>();
  for (const r of rows) {
    const 키 = `${r.api_name}|${r.window_type}`;
    const 앞 = 가장새.get(키);
    if (!앞 || r.window_start > 앞.window_start) 가장새.set(키, r);
  }
  const 오늘 = now.toISOString().slice(0, 10);
  const 이번달 = now.toISOString().slice(0, 7);
  return [...가장새.values()].map((r) => ({
    ...r,
    // **수위를 재는 행은 늘 지금 것이다** (25.594, 교차검증). `d1_db_size` 는 리셋이 없는 용량이라 일일 배치가 적은 날짜가 지나도
    // 가장 새 값이 지금 수위다 — 창 날짜로 가르면 한국 낮 내내·주말에 회색 "지난 창" 이 되어 가장 먼저 필요한 80% 경고가 가려졌다
    // 단 **며칠 안의 값일 때만** — 적는 쪽(`measure_db_size`)이 D1 이 아니거나 측정에 실패하면 조용히 넘어가, 몇 주 전 값이 영영 "지금" 이었다 (25.596, 교차검증)
    current: (LEVEL_GAUGES.has(r.api_name)
      && (now.getTime() - Date.parse(`${r.window_start.slice(0, 10)}T00:00:00Z`)) / 86_400_000 <= LEVEL_GAUGE_MAX_AGE_DAYS)
      || (r.window_type === "month" ? r.window_start.slice(0, 7) === 이번달 : r.window_start === 오늘),
  }));
}

export const RECENT_HEALTH_ALERTS = `SELECT job, market, local_date, kind, message, created_at, sent_at
FROM health_alerts ORDER BY created_at DESC LIMIT 10`;

export const CRON_HEARTBEATS = `SELECT job, market, called_at, outcome, detail, calls_today, day FROM cron_heartbeats
ORDER BY job, market`;

/**
 * **바깥 크론이 얼마마다 불러야 하나** (docs/infra.md 25.141).
 *
 * cron-job.org 가 부르는 Vercel 경로들이다. 이 표가 없으면 `/status` 의 "크론 호출" 은
 * 시각만 적어 놓은 줄 세 개다 — **`health` 가 `1970-01-01 · never` 로 몇 달을 앉아 있어도**
 * 회색 작은 글씨일 뿐이다. 그 한 줄이 뜻하는 것은 "무응답 감시가 아예 안 돈다" 이고,
 * 그러면 아침 리포트가 안 와도 아무도 모른다.
 *
 * 주기의 정의처는 **문서**다 (`docs/intraday.md` 4장의 등록 표).
 * `tests/test_cron_expected.py` 가 둘을 대조한다 — 등록을 바꾸면 여기가 깨진다.
 */
export interface CronExpect {
  job: string;
  market: string;
  label: string;
  /** 얼마마다 불리나 (분) */
  everyMinutes: number;
  /**
   * `always` 는 하루 종일, `market` 은 그 시장 장중에만 불린다.
   *
   * **`market` 은 여기서 판정하지 않는다.** 장 밖 시간이 훨씬 길어 "몇 시간 전" 만으로는
   * 멈춘 것과 밤인 것을 못 가른다. 장중 호출은 [알림] 화면이 본다 (docs/intraday.md 7장).
   */
  when: "always" | "market";
  /** 안 불릴 때 무엇이 멈추나. 판정의 무게다 */
  stakes: string;
}

export const CRON_EXPECTED: CronExpect[] = [
  { job: "health", market: "ALL", label: "무응답 감시", everyMinutes: 60, when: "always",
    stakes: "이것이 멈추면 **배치가 안 돌아도 알림이 오지 않습니다.** 아침 리포트가 빠진 날을 아무도 모릅니다" },
  // 1분이던 것을 2026-10-02 사용자 결정으로 매시로 줄였다 (docs/infra.md 25.880)
  { job: "news", market: "US", label: "미국 뉴스 수집", everyMinutes: 60, when: "always",
    stakes: "한 호출에 한 종목씩 받습니다. 멈추면 미국 감성 점수가 옛 기사로 굳습니다" },
  { job: "news", market: "KR", label: "국내 뉴스 수집", everyMinutes: 60, when: "always",
    stakes: "산업 피드가 세 시간쯤만 덮습니다. 멈추면 그 사이 기사를 영영 못 받습니다" },
  { job: "intraday", market: "KR", label: "국내 장중 감시", everyMinutes: 5, when: "market",
    stakes: "매수 구간 진입·목표가·손절선 알림이 멈춥니다" },
  { job: "intraday", market: "US", label: "미국 장중 감시", everyMinutes: 5, when: "market",
    stakes: "매수 구간 진입·목표가·손절선 알림이 멈춥니다" },
];


/**
 * 크론 호출 한 줄의 판정 (docs/infra.md 25.141).
 *
 * 문턱은 **주기의 세 배**다. 한 번 거르는 일은 바깥 서비스에서 흔하고(재시도·지연),
 * 세 번을 내리 거르면 멈춘 것이다. 25.140 의 신선도 칸과 같은 뜻의 판단이다.
 * 다만 최소 15분은 둔다 — 1분 크론을 3분으로 재면 화면을 열 때마다 빨개진다.
 */
export function cronVerdict(
  row: { job: string; market: string; called_at: string; outcome: string },
  now: Date,
  // 시험이 한 시간보다 짧은 크론을 넣어 볼 수 있게 받는다. 운영은 늘 CRON_EXPECTED 다
  table: readonly CronExpect[] = CRON_EXPECTED,
): FreshnessVerdict {
  const 기대 = table.find((c) => c.job === row.job && c.market === row.market);
  // 장중 감시를 껐으면 비어 있는 것이 정상이다 (25.1026)
  if (row.job === "intraday" && !INTRADAY_ENABLED) return { tone: "mute", text: "꺼짐", why: INTRADAY_OFF_NOTE };
  if (!기대) {
    return { tone: "mute", text: "", why: "이 크론의 기대 주기가 적혀 있지 않습니다" };
  }
  if (row.outcome === "never") {
    return {
      tone: "bad",
      text: "한 번도 안 불렸다",
      why: `${기대.label}는 ${기대.everyMinutes}분마다 불려야 합니다. 한 번도 불린 적이 없습니다.\n${기대.stakes}`,
    };
  }

  const 지난분 = Math.floor((now.getTime() - Date.parse(row.called_at)) / 60_000);
  if (!Number.isFinite(지난분)) return { tone: "mute", text: "", why: "호출 시각을 읽지 못했습니다" };

  // **불린 것과 해낸 것은 다르다** (2026-09-23, docs/infra.md 25.183).
  //
  // 여기서 `outcome` 을 보는 곳은 `"never"` 하나뿐이었다. 그래서 크론이 제시간에
  // 불리기만 하면 **매 호출이 실패해도 초록**이었다 — `cron/news` 가 예외를 잡아
  // `outcome: "error"` 로 심장박동을 남기고, 이 판정은 `called_at` 만 보고 "1분 전" 이라며
  // `ok` 를 돌려줬다. 뉴스가 일주일 내내 안 들어와도 /status 는 멀쩡했다.
  // 25.0 「잃고서 초록으로 알린다」 — 그것도 **그 고장을 알려 주라고 만든 화면**에서.
  //
  // 마지막 호출이 실패했으면 빨갛다. 다음 성공에서 저절로 풀린다(심장박동은 한 줄이라
  // 연속 실패 횟수를 셀 수 없다 — 그러니 "마지막이 실패했다" 를 그대로 말한다).
  if (row.outcome === "error") {
    return {
      tone: "bad",
      text: sinceMinutesText(row.called_at, now),
      why: `${기대.label}가 불리고는 있지만 **마지막 호출이 실패했습니다.**\n` +
        `${기대.stakes}\n` +
        "아래 detail 의 오류를 보세요. 제시간에 불리는 것과 해내는 것은 다릅니다",
    };
  }

  if (기대.when === "market") {
    // **여기서 재지 않는다.** 장 밖이 훨씬 길어 "밤" 과 "멈춤" 을 못 가른다.
    // 장중 호출은 [알림] 화면이 그 시장의 장중인지 보고 판단한다 (docs/intraday.md 7장)
    return {
      tone: "mute",
      text: sinceText(row.called_at, now),
      why: `${기대.label}는 장중에만 불립니다. 장 밖에 비어 있는 것은 정상입니다.\n` +
        "장중인데 비어 있는지는 [알림] 화면 윗줄이 판정합니다 (intradayVerdict)",
    };
  }

  const 문턱 = Math.max(기대.everyMinutes * 3, 15);
  // 건너뛴 것은 정상이다(대상 0건·조용시간). 다만 **왜 건너뛰었는지**는 적는다 —
  // "대상 없음" 이 며칠씩 이어지면 그것 자체가 고장의 자취다
  const 건너뜀 = row.outcome.startsWith("skipped:") ? `\n건너뛰었습니다: ${row.outcome.slice(8)}` : "";
  const 근거 = `${기대.label}는 ${기대.everyMinutes}분마다 불려야 합니다. ${문턱}분을 넘으면 멈춘 것으로 봅니다.\n${기대.stakes}${건너뜀}`;
  // **잣대의 굵기에 말투를 맞춘다.** 한 시간 안에 여러 번 도는 것에 "1시간 이내" 를
  // 붙이면 노란 칸에 멀쩡한 글자가 적힌다 (docs/infra.md 25.141 덧)
  const 말 = 기대.everyMinutes < 60 ? sinceMinutesText(row.called_at, now) : sinceText(row.called_at, now);

  if (지난분 <= 문턱) return { tone: "ok", text: 말, why: 근거 };
  if (지난분 <= 문턱 * 2) return { tone: "warn", text: 말, why: `${근거}\n한 번 거른 듯합니다` };
  return { tone: "bad", text: 말, why: `${근거}\n멈췄습니다 — cron-job.org 의 작업과 헤더를 확인하세요` };
}

/**
 * **장중 크론이 지금 부지런한가** (docs/infra.md 25.144).
 *
 * `cronVerdict` 는 장중 크론을 판정하지 않고 "[알림] 화면이 본다" 고 미뤘다. 그런데
 * 그 화면도 시각을 회색으로 적기만 했다 — **미룬 곳이 미룰 곳이 아니었다.**
 * 25.0 「문서가 "누가 한다" 를 약속하는데 그 주체가 할 수 없다」.
 *
 * 여기서 판정한다. `inSession` 은 부르는 쪽이 `intraday.activeSession` 으로 정해 넘긴다 —
 * **정규장인지 아는 규칙은 그 함수 하나뿐**이어야 한다(장중 경로가 쓰는 바로 그것이다).
 * 주기도 `CRON_EXPECTED` 에서 온다. 이 함수는 잣대를 하나도 새로 만들지 않는다.
 */
export function intradayVerdict(
  market: string,
  beat: { called_at: string; outcome: string } | undefined,
  inSession: boolean,
  now: Date,
): FreshnessVerdict {
  if (!INTRADAY_ENABLED) return { tone: "mute", text: "꺼짐", why: INTRADAY_OFF_NOTE };
  const 기대 = CRON_EXPECTED.find((c) => c.job === "intraday" && c.market === market);
  const 주기 = 기대?.everyMinutes ?? 5;
  const 문턱 = 주기 * 3;

  if (!inSession) {
    return {
      tone: "mute",
      text: beat ? sinceMinutesText(beat.called_at, now) : "",
      why: "지금은 정규장이 아닙니다. 장 밖에 비어 있는 것은 정상입니다",
    };
  }
  if (!beat) {
    return {
      tone: "bad",
      text: "호출 없음",
      why:
        `정규장인데 호출 기록이 없습니다. ${주기}분마다 불려야 합니다.\n` +
        "매수 구간 진입·목표가·손절선 알림이 멈춘 상태입니다 — cron-job.org 의 작업과 헤더를 확인하세요",
    };
  }

  const 지난분 = Math.floor((now.getTime() - Date.parse(beat.called_at)) / 60_000);
  if (!Number.isFinite(지난분)) return { tone: "mute", text: "", why: "호출 시각을 읽지 못했습니다" };

  const 말 = sinceMinutesText(beat.called_at, now);
  // **불린 것과 해낸 것은 다르다** (25.183 의 장중판, docs/infra.md 25.338). 발송 실패도 `error` 로 남는다
  if (beat.outcome === "error") {
    return {
      tone: "bad",
      text: 말,
      why: "장중 감시가 불리고는 있지만 **마지막 호출이 실패했습니다** — 알림이 안 나가고 있을 수 있습니다.\n" +
        "상태 화면의 호출 기록 detail 에서 오류(발송 실패 등)를 보세요",
    };
  }
  const 근거 = `정규장에는 ${주기}분마다 불려야 합니다. ${문턱}분을 넘으면 멈춘 것으로 봅니다`;
  if (지난분 <= 문턱) return { tone: "ok", text: 말, why: 근거 };
  if (지난분 <= 문턱 * 2) return { tone: "warn", text: 말, why: `${근거}\n한 번 거른 듯합니다` };
  return {
    tone: "bad",
    text: 말,
    why: `${근거}\n장중 감시가 멈췄습니다 — 매수 구간 진입·목표가·손절선 알림이 안 옵니다`,
  };
}


/**
 * **얼마마다 채워지나.** 허용 일수의 근거다 (docs/infra.md 25.140).
 *
 * 날짜만 스물여섯 개 늘어놓으면 **아무도 읽지 못한다.** 칸마다 "며칠이면 늦은 것인가" 가
 * 다르기 때문이다 — 시세는 사흘만 멈춰도 고장이고, 배당은 반년이 정상이다.
 * 그 잣대를 화면이 알아야 색을 칠할 수 있고, 잣대의 근거는 **그 칸을 채우는 예약의 주기**다.
 *
 *   `session`  거래일마다 (일일 배치)
 *   `week`     주 1회 예약
 *   `month`    월 1회 예약
 *   `year`     연 1~2회 (배당처럼 자료 자체가 한 해 단위)
 *   `ahead`    **미래가 정상**인 칸 (거래일 달력). 여기서 판정하지 않는다 — 아래를 보라
 *   `manual`   예약이 없다. 사람이 손으로 돌린다 (25.138)
 */
export type Cadence = "session" | "week" | "month" | "year" | "ahead" | "manual";

/**
 * 거래일마다 채워지는 칸의 허용 일수.
 *
 * **배치의 `batch/services/metrics.MAX_SESSION_GAP_DAYS` 와 같은 값이어야 한다**
 * (`tests/test_freshness_verdict.py` 가 대조한다). 실측값이다 — `exchange_calendars` 의
 * XKRX 에서 가장 긴 휴장 간격이 11일이다. 추석·설 연휴가 주말에 붙는 해가 그렇다.
 */
export const MAX_SESSION_GAP_DAYS = 11;

/**
 * 거래일마다 채워지는 **기준일 값**의 허용 나이 (docs/infra.md 25.242).
 *
 * 간격(위)과 다르다. 시세·지수·감성의 날짜는 그날 아침 배치가 넘기는 **직전 거래일**이라 한 세션 늦다.
 * 연속한 세 거래일 Z·A·B 에서 B 아침 배치 직전의 값은 Z 이고 나이는 `B − Z` — XKRX 2017-09-29 → 10-11 의 **12일**이
 * 2016~2026 실측 최댓값이다(`exchange_calendars`, `tests/test_stale_threshold.py` 가 다시 잰다). 11 로 두면 그 연휴에
 * 정상 배치에도 "한 번 거른 듯합니다" 가 떴다. 추천 화면 `recommend.STALE_AFTER_DAYS` 와 같은 값이다.
 */
export const MAX_AS_OF_AGE_DAYS = 12;

/**
 * 환율은 배치의 상수를 따른다 (`batch/services/fx.MAX_STALE_DAYS`). **실측이 아니라 추정이다** — 배치 쪽 주석이
 * "추정이다 [확인필요]" 라고 적는다. 예전 이 주석은 "실측 상수" 라 불러 거래일 칸(11·12일, 달력에서 잰 값)과 같은 무게로 읽혔다
 * (docs/infra.md 25.280). `tests/test_freshness_verdict.py` 가 두 값을 대 본다
 */
export const FX_STALE_DAYS = 7;

/**
 * 허용 일수. 넘으면 노랑, **두 배를 넘으면** 빨강.
 *
 * `week`·`month`·`year` 의 값은 실측이 아니라 **판단**이다 — Actions 지연이나 DB 한도로
 * 한 번 거르는 일은 흔하고(25.6 "건너뜀"), 그때마다 빨개지면 사람이 곧 이 화면을 안 본다.
 * **두 번 연속 걸렀으면 고장이다.** 그래서 주기의 두 배로 잡았다.
 */
export const STALE_AFTER_DAYS: Record<"session" | "week" | "month" | "year", number> = {
  session: MAX_AS_OF_AGE_DAYS,
  week: 14,
  month: 62,
  year: 400,
};

export interface FreshnessRow {
  key: string;
  label: string;
  sql: string;
  /** 얼마마다 채워지나 */
  every: Cadence;
  /** 허용 일수를 따로 정할 때 (배치에 그 칸만의 실측 상수가 있는 경우) */
  staleDays?: number;
  /**
   * D1 임시 운영 중에는 안 채워진다 (docs/infra.md 25.11·25.14).
   *
   * **낡은 것이 정상이다.** 빨갛게 칠하면 거짓 경보이고, 거짓 경보가 쌓이면 사람은
   * 진짜로 멈춘 날에도 이 화면을 안 읽는다. 무응답 감시는 이미 이것을 안다
   * (`dueAlerts` 의 `markets`) — **화면만 몰랐다.**
   */
  restsOnD1?: boolean;
}

/**
 * 데이터 신선도. 표마다 "가장 최근 기준일" 하나씩.
 *
 * 화면이 빈칸을 보고 "아직 없음" 과 "오래됨" 을 구분할 수 있어야 해서 표 이름을 그대로 쓴다.
 * 표가 없을 수도 있으므로(마이그레이션 전) 경로가 하나씩 따로 부르고 실패는 넘긴다.
 *
 * **시각 열은 한국 날짜로 바꿔 읽는다** (docs/infra.md 25.240). `fetched_at`·`built_at`·`finished_at` 은 UTC ISO 다.
 * 예전에는 앞 10자(`substr(…, 1, 10)`)를 날짜로 써서, 08:27 KST(= 전날 23:27 UTC)에 만든 감시 목록이 **만든 날 아침부터
 * "1일 전"** 이었다. 판정의 오늘은 한국 날짜(`freshnessVerdict`)라 두 잣대가 섞였다. 한국은 서머타임이 없어 +9 시간이 고정이다
 */
export const FRESHNESS: FreshnessRow[] = [
  { key: "prices_kr", label: "국내 시세", sql: "SELECT MAX(p.date) AS v FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE s.country = 'KR'", every: "session" },
  { key: "prices_us", label: "미국 시세", sql: "SELECT MAX(p.date) AS v FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE s.country = 'US'", every: "session", restsOnD1: true },
  { key: "fx", label: "환율(USDKRW)", sql: "SELECT MAX(date) AS v FROM fx_rates WHERE pair = 'USDKRW'", every: "session", staleDays: FX_STALE_DAYS },
  // **나라별로 나눠서 본다 (2026-09-21).** 점수·신호·유니버스는 나라마다 따로 계산되고
  // (`scores.run(market)`·`signals.run(market)`), 지금은 미국이 통째로 쉬고 있다(25.14).
  // 전체 MAX 로 한 줄만 보여 주면 **국내만 보고 "최신" 이라 말하고 미국이 몇 달 멈춘 것을
  // 가린다.** 이 화면은 "데이터가 싱싱한가" 를 보려고 여는 곳이다. 시세·거래일 달력은
  // 처음부터 나라별로 두고 있었다 — 나머지 셋만 빠져 있었다.
  // 값은 읽기 세 번이 늘지만 읽기는 넉넉하다(D1 하루 500만 행).
  // **점수는 거래일마다** (docs/infra.md 25.917, 감사). 25.889 가 주간 예약을 빼고 일일 배치가 매일 계산한다 — "week" 이면 점수 단계가 일일 배치 안에서
  // 계속 실패해도(일일 배치는 partial 로 끝나 무응답 감시를 피한다) 14일까지 초록이었다. 신호는 0건인 날이 정상이라 week 로 둔다
  { key: "scores_kr", label: "점수(국내)", sql: "SELECT MAX(sc.as_of_date) AS v FROM scores sc JOIN stocks s ON s.id = sc.stock_id WHERE s.country = 'KR'", every: "session" },
  { key: "scores_us", label: "점수(미국)", sql: "SELECT MAX(sc.as_of_date) AS v FROM scores sc JOIN stocks s ON s.id = sc.stock_id WHERE s.country = 'US'", every: "session", restsOnD1: true },
  { key: "signals_kr", label: "매수 신호(국내)", sql: "SELECT MAX(sg.as_of_date) AS v FROM signals sg JOIN stocks s ON s.id = sg.stock_id WHERE s.country = 'KR'", every: "week" },
  { key: "signals_us", label: "매수 신호(미국)", sql: "SELECT MAX(sg.as_of_date) AS v FROM signals sg JOIN stocks s ON s.id = sg.stock_id WHERE s.country = 'US'", every: "week", restsOnD1: true },
  // **나라마다 따로 본다** (docs/infra.md 25.247). 한 줄 MAX 였을 때는 국내 날짜가 미국 감성의 멈춤을 덮었다.
  // `sentiment_scores` 는 뉴스 크론이 아니라 `jobs/sentiment.run(market)` 이 시장마다 쓴다 — 크론이 초록이어도 채점·집계는 멈출 수 있다
  { key: "sentiment_kr", label: "종목 감성(국내)", sql: "SELECT MAX(x.as_of_date) AS v FROM sentiment_scores x JOIN stocks s ON s.id = x.stock_id WHERE s.country = 'KR'", every: "session" },
  { key: "sentiment_us", label: "종목 감성(미국)", sql: "SELECT MAX(x.as_of_date) AS v FROM sentiment_scores x JOIN stocks s ON s.id = x.stock_id WHERE s.country = 'US'", every: "session", restsOnD1: true },
  // **수급은 하루라도 빠지면 영영 빈다** (docs/infra.md 25.987) — KIS 가 30일만 주므로 한 달 넘게 멈추면 그 구간은 다시 못 받는다
  { key: "kr_flows", label: "국내 수급(투자자별·공매도·신용, KIS)", sql: "SELECT MAX(date) AS v FROM kr_flows", every: "session" },
  { key: "universe_kr", label: "유니버스(국내)", sql: "SELECT MAX(um.snapshot_date) AS v FROM universe_members um JOIN stocks s ON s.id = um.stock_id WHERE s.country = 'KR'", every: "week" },
  { key: "universe_us", label: "유니버스(미국)", sql: "SELECT MAX(um.snapshot_date) AS v FROM universe_members um JOIN stocks s ON s.id = um.stock_id WHERE s.country = 'US'", every: "week", restsOnD1: true },
  // **이 두 줄만 뜻이 반대다.** 나머지는 "최근일수록 싱싱하다" 인데 달력은 **미래 날짜**가
  // 정상이고, 오늘에 가까워질수록 위험하다 — 다 차면 장중 감시가 멈춘다(25.104).
  // 이름에 그 방향을 적어 둔다. 같은 격자에서 같은 잣대로 읽히면 안 된다
  { key: "sessions_kr", label: "국내 거래일 달력(이 날까지 채워짐)", sql: "SELECT MAX(date) AS v FROM market_sessions WHERE market = 'KR'", every: "ahead" },
  { key: "sessions_us", label: "미국 거래일 달력(이 날까지 채워짐)", sql: "SELECT MAX(date) AS v FROM market_sessions WHERE market = 'US'", every: "ahead", restsOnD1: true },
  // **추천에 들어가는 재료는 전부 여기 있어야 한다** (2026-09-21, docs/infra.md 25.78).
  // 아래 여섯은 `scores`·`signals` 가 실제로 읽는데 이 화면에 없었다.
  // 재무는 기준일(`report_date`)이 회계연도라 늘 옛날이다. **언제 받아 왔나**(`fetched_at`)를
  // 보는 것이 맞고, 다른 칸과 같은 모양이 되게 날짜만 잘라 낸다. 재료가 몇 달 멈춰도
  // 점수·신호는 **옛 값으로 매일 새로 계산되어** 기준일이 오늘로 찍힌다 — 화면은 싱싱해 보인다.
  { key: "financials_kr", label: "재무(국내)", sql: "SELECT date(MAX(f.fetched_at), '+9 hours') AS v FROM financials f JOIN stocks s ON s.id = f.stock_id WHERE s.country = 'KR'", every: "week" },
  { key: "financials_us", label: "재무(미국)", sql: "SELECT date(MAX(f.fetched_at), '+9 hours') AS v FROM financials f JOIN stocks s ON s.id = f.stock_id WHERE s.country = 'US'", every: "month", restsOnD1: true },
  // 2026-09-21: 배당이 **추천의 재료가 됐다** (밸류 축의 배당수익률, docs/factors.md 11.1).
  // 재료가 멈춰도 점수는 옛 값으로 매일 새로 계산돼 기준일이 오늘로 찍힌다 —
  // 그래서 재료마다 신선도 칸이 있어야 한다(docs/infra.md 25.78).
  // 재무와 같은 이유로 `report_date`(회계연도) 가 아니라 **받아 온 날**로 본다
  { key: "dividends_kr", label: "배당(국내)", sql: "SELECT date(MAX(d.fetched_at), '+9 hours') AS v FROM stock_dividends d JOIN stocks s ON s.id = d.stock_id WHERE s.country = 'KR'", every: "year" },
  { key: "dividends_us", label: "배당(미국)", sql: "SELECT date(MAX(d.fetched_at), '+9 hours') AS v FROM stock_dividends d JOIN stocks s ON s.id = d.stock_id WHERE s.country = 'US'", every: "year", restsOnD1: true },
  { key: "metrics_kr", label: "성과 지표(국내)", sql: "SELECT MAX(m.as_of_date) AS v FROM performance_metrics m JOIN stocks s ON s.id = m.stock_id WHERE s.country = 'KR'", every: "week" },
  { key: "metrics_us", label: "성과 지표(미국)", sql: "SELECT MAX(m.as_of_date) AS v FROM performance_metrics m JOIN stocks s ON s.id = m.stock_id WHERE s.country = 'US'", every: "week", restsOnD1: true },
  // 지수는 추세 필터(docs/signals.md 3.5)와 **베타**(25.65)의 재료다. 60일치가 없으면 베타가 빈다
  // **수정주가는 예약이 없다** (docs/infra.md 25.138). 사람이 `adjust-kr.yml` 을 손으로 돌린다.
  // 그래서 **마지막으로 언제 돌렸나**를 보인다 (25.924). 예전 식 "adj_close 가 있는 가장 늦은 날" 은
  // 실행 시점이 아니라 **가장 최근 기업행위의 전날**이었다 — 수정주가는 마지막 기업행위 이후 구간을
  // 종가와 같아 비워 두므로(`adjust_kr.rows_to_write`) 방금 다 돌려도 날짜가 몇 주씩 뒤처져 보였다
  { key: "adj_close_kr", label: "국내 수정주가(마지막 실행)", sql: "SELECT date(MAX(finished_at), '+9 hours') AS v FROM batch_runs WHERE job_name = 'adjust_kr' AND status = 'success'", every: "manual" },
  // **나라를 가린 칸이 여섯 더 있었다** (2026-09-23, docs/infra.md 25.182).
  // 2026-09-21 에 점수·신호·유니버스를 나눌 때 적은 이유가 그대로 이것들에도 맞는다 —
  // 미국이 통째로 쉬는 지금(25.14) **전체 MAX 는 국내 날짜를 보여 주고 "최신" 이라 말한다.**
  { key: "index_prices_kr", label: "지수(국내, 추세 필터·베타)", sql: "SELECT MAX(date) AS v FROM index_prices WHERE index_code IN ('KOSPI', 'KOSDAQ')", every: "session" },
  { key: "index_prices_us", label: "지수(미국, 추세 필터·베타)", sql: "SELECT MAX(date) AS v FROM index_prices WHERE index_code = 'SP500'", every: "session", restsOnD1: true },
  { key: "valuation_bands_kr", label: "밸류에이션 밴드(국내, 장기 신호)", sql: "SELECT MAX(b.as_of_date) AS v FROM valuation_bands b JOIN stocks s ON s.id = b.stock_id WHERE s.country = 'KR'", every: "week" },
  { key: "valuation_bands_us", label: "밸류에이션 밴드(미국, 장기 신호)", sql: "SELECT MAX(b.as_of_date) AS v FROM valuation_bands b JOIN stocks s ON s.id = b.stock_id WHERE s.country = 'US'", every: "week", restsOnD1: true },
  // 아래 다섯은 2026-09-23 에 더했다 (docs/infra.md 25.139).
  //
  // `tests/test_watch_names.py` 는 "주 1회·월 1회짜리는 /status 의 데이터 신선도로 본다"
  // 고 적어 두었는데, **정작 여기 없는 것이 일곱이었다.** 무응답 감시는 셋만 보고
  // 나머지는 여기서 본다는 약속이라, 여기 없으면 **아무 데서도 안 보인다.**
  // 판단의 재료이거나 화면 한 탭이 통째로 매달린 것만 골랐다.
  { key: "sectors_kr", label: "업종(국내, 업종별 z-score 의 재료)", sql: "SELECT date(MAX(sector_updated_at), '+9 hours') AS v FROM stocks WHERE country = 'KR' AND sector IS NOT NULL", every: "month" },
  // 미국 업종도 모은다 — SEC `sic` (jobs/sectors `--market US`, docs/factors.md). 2026-09-26 까지
  // 이 칸이 없었고, 25.182 의 "짝이 있는가" 검사를 **틀린 사유**("미국 업종은 아직 모으지 않는다")로
  // 비켜 가 있었다 (docs/infra.md 25.194)
  { key: "sectors_us", label: "업종(미국, 섹터 상한·업종별 z-score 의 재료)", sql: "SELECT date(MAX(sector_updated_at), '+9 hours') AS v FROM stocks WHERE country = 'US' AND sector IS NOT NULL", every: "month", restsOnD1: true },
  { key: "etf_profiles_kr", label: "ETF 프로필(국내, 장기 적립 탭)", sql: "SELECT MAX(pf.as_of_date) AS v FROM etf_profiles pf JOIN etfs e ON e.id = pf.etf_id WHERE e.country = 'KR'", every: "month" },
  { key: "etf_profiles_us", label: "ETF 프로필(미국, 장기 적립 탭)", sql: "SELECT MAX(pf.as_of_date) AS v FROM etf_profiles pf JOIN etfs e ON e.id = pf.etf_id WHERE e.country = 'US'", every: "month", restsOnD1: true },
  { key: "accum_picks_kr", label: "적립 종목 판정(국내)", sql: "SELECT MAX(p.as_of_date) AS v FROM stock_accum_picks p JOIN stocks s ON s.id = p.stock_id WHERE s.country = 'KR'", every: "month" },
  { key: "accum_picks_us", label: "적립 종목 판정(미국)", sql: "SELECT MAX(p.as_of_date) AS v FROM stock_accum_picks p JOIN stocks s ON s.id = p.stock_id WHERE s.country = 'US'", every: "month", restsOnD1: true },
  { key: "earnings_calendar_kr", label: "실적 일정(국내)", sql: "SELECT date(MAX(e.fetched_at), '+9 hours') AS v FROM earnings_calendar e JOIN stocks s ON s.id = e.stock_id WHERE s.country = 'KR'", every: "week" },
  { key: "earnings_calendar_us", label: "실적 일정(미국)", sql: "SELECT date(MAX(e.fetched_at), '+9 hours') AS v FROM earnings_calendar e JOIN stocks s ON s.id = e.stock_id WHERE s.country = 'US'", every: "week", restsOnD1: true },
  { key: "signal_outcomes_kr", label: "신호 성적표(국내, 복기)", sql: "SELECT date(MAX(computed_at), '+9 hours') AS v FROM signal_outcome_stats WHERE country = 'KR'", every: "week" },
  { key: "signal_outcomes_us", label: "신호 성적표(미국, 복기)", sql: "SELECT date(MAX(computed_at), '+9 hours') AS v FROM signal_outcome_stats WHERE country = 'US'", every: "week", restsOnD1: true },
  // 2026-09-23 (docs/infra.md 25.162). **장중 알림이 서 있는 땅**이다. 감시 목록·권장 매수
  // 구간·20일 평균 거래량이 전부 이 표에서 나오는데, 장중 경로는 Actions 밖에서 돌아
  // 일일 배치가 멈춰도 계속 알린다 — 옛 구간으로. `built_at` 은 처음부터 있었다
  // **백업만 자취가 없었다** (2026-09-23, docs/infra.md 25.167). 다른 예약 작업은 모두
  // 저장하는 표가 있어 신선도 칸으로 보이는데, 백업은 산출물이 GitHub 아티팩트라
  // DB 에 아무것도 안 남겼다 — 주 1회가 몇 주째 멈춰도 알 길이 없었다.
  // **잃으면 끝인 자료를 지키는 유일한 장치다**(docs/backup.md 0장).
  // 이제 `backup_db.py --record-run` 이 `batch_runs` 에 실행 기록을 남긴다
  // **기본 백테스트** (25.942) — 매달 3일 예약. 기법 발굴 판정이 이 실행만 센다(factors.md 12장 머리). 기본 파라미터 실행만 본다
  { key: "backtest_kr", label: "기본 백테스트(국내, 마지막 기본 실행)", sql: "SELECT date(MAX(finished_at), '+9 hours') AS v FROM batch_runs WHERE job_name = 'backtest' AND market = 'KR' AND status = 'success' AND json_extract(step_log, '$.params.default') = 1", every: "month" },
  { key: "backtest_us", label: "기본 백테스트(미국, 마지막 기본 실행)", sql: "SELECT date(MAX(finished_at), '+9 hours') AS v FROM batch_runs WHERE job_name = 'backtest' AND market = 'US' AND status = 'success' AND json_extract(step_log, '$.params.default') = 1", every: "month", restsOnD1: true },
  // 주간 운영 요약 (25.943) — 일요일 아침 텔레그램. 안 오면 여기서 낡는다
  { key: "weekly_summary", label: "주간 운영 요약(마지막 발송)", sql: "SELECT date(MAX(finished_at), '+9 hours') AS v FROM batch_runs WHERE job_name = 'weekly_summary' AND status = 'success'", every: "week" },
  // 백업 복구 리허설 (25.946) — 분기 1회라 `year`(400일) 로 본다. 두 번 건너뛰어야 빨개진다
  { key: "restore_drill", label: "백업 복구 리허설(마지막 통과)", sql: "SELECT date(MAX(finished_at), '+9 hours') AS v FROM batch_runs WHERE job_name = 'restore_drill' AND status = 'success'", every: "year" },
  { key: "backup", label: "DB 백업(마지막 성공)", sql: "SELECT date(MAX(finished_at), '+9 hours') AS v FROM batch_runs WHERE job_name = 'backup' AND status = 'success'", every: "week" },
  { key: "monitor_kr", label: "장중 감시 목록(국내)", sql: "SELECT date(MAX(built_at), '+9 hours') AS v FROM monitor_targets WHERE market = 'KR'", every: "session" },
  { key: "monitor_us", label: "장중 감시 목록(미국)", sql: "SELECT date(MAX(built_at), '+9 hours') AS v FROM monitor_targets WHERE market = 'US'", every: "session", restsOnD1: true },
];

const 신선도표 = new Map(FRESHNESS.map((f) => [f.key, f]));

export interface FreshnessVerdict {
  tone: "ok" | "warn" | "bad" | "mute";
  /** 칸에 적을 짧은 말. "3일 전" · "쉬는 중" · "없음" */
  text: string;
  /** 왜 그렇게 판정했나. 화면이 툴팁으로 펼쳐 보여 준다 (CLAUDE.md "근거를 펼쳐 볼 수 있어야 한다") */
  why: string;
}

/** 두 날짜 사이의 **달력일**. 시각은 안 본다 */
function 며칠(앞: string, 뒤: string): number | null {
  const a = Date.parse(`${앞.slice(0, 10)}T00:00:00Z`);
  const b = Date.parse(`${뒤.slice(0, 10)}T00:00:00Z`);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  return Math.round((b - a) / 86_400_000);
}

/**
 * 신선도 칸 하나의 판정 (docs/infra.md 25.140).
 *
 * **오늘은 한국 날짜로 센다.** 이 화면을 보는 사람이 한국에 있다
 * (배치의 `calendar.user_today()` 와 같은 뜻, 25.135 무렵).
 *
 * 모르는 키는 판정하지 않는다 — **지어내는 것보다 조용한 편이 낫다.**
 */
export function freshnessVerdict(
  key: string,
  value: string | null,
  now: Date,
  backend: string,
): FreshnessVerdict {
  const row = 신선도표.get(key);
  if (!row) return { tone: "mute", text: value ?? "없음", why: "판정 기준이 없는 칸입니다" };

  if (row.restsOnD1 && backend === "d1") {
    return {
      tone: "mute",
      text: "쉬는 중",
      why:
        "D1 임시 운영 중에는 이 자료를 채우지 않습니다 (운영 기록 25.11·25.14).\n" +
        // D1 에서는 이 칸을 읽지 않는다 — 읽기 한도 보호 (25.857). 그래서 기준일 대신 그 사실을 말한다
        (value === null ? "D1 읽기 한도를 아끼려고 이 칸은 읽지 않습니다 (운영 기록 25.857)." : `낡은 것이 정상입니다. 마지막 기준일: ${value}`),
    };
  }
  if (value === null) {
    return { tone: "bad", text: "없음", why: "한 번도 채워지지 않았습니다" };
  }

  const 오늘 = localDate("KR", now);
  const 지난날 = 며칠(value, 오늘);
  if (지난날 === null) return { tone: "mute", text: value, why: "날짜를 읽지 못했습니다" };

  if (row.every === "ahead") {
    // **여기서 판정하지 않는다.** 남은 '거래일' 수로 보는 규칙이 이미 있고
    // (`CALENDAR_LOW_SESSIONS`, 25.104), 달력일로 또 재면 두 잣대가 어긋난다
    const 남음 = -지난날;
    return {
      tone: "mute",
      text: 남음 >= 0 ? `앞으로 ${남음}일` : `${지난날}일 지남`,
      why:
        "거래일 달력은 **미래가 정상**입니다. 다 마르기 전에 알리는 일은\n" +
        `무응답 감시가 남은 거래일 ${CALENDAR_LOW_SESSIONS}일 기준으로 합니다 (운영 기록 25.104)`,
    };
  }

  const 말 = 지난날 === 0 ? "오늘" : 지난날 > 0 ? `${지난날}일 전` : `${-지난날}일 뒤`;

  if (row.every === "manual") {
    return {
      tone: "mute",
      text: 말,
      why:
        "예약이 없습니다. 사람이 손으로 돌립니다 (운영 기록 25.138).\n" +
        "다시 내야 할 때는 아침 리포트가 말해 줍니다 — 기업행위를 알아채면 경고가 붙습니다",
    };
  }

  const 문턱 = row.staleDays ?? STALE_AFTER_DAYS[row.every];
  const 주기 = { session: "거래일마다", week: "주 1회", month: "월 1회", year: "연 1~2회" }[row.every];
  const 근거 = `${주기} 채워지는 자료입니다. ${문턱}일까지는 정상, ${문턱 * 2}일을 넘으면 고장으로 봅니다`;

  if (지난날 <= 문턱) return { tone: "ok", text: 말, why: 근거 };
  if (지난날 <= 문턱 * 2) return { tone: "warn", text: 말, why: `${근거}\n한 번 거른 듯합니다` };
  return { tone: "bad", text: 말, why: `${근거}\n두 번 넘게 걸렀습니다 — 멈춘 것으로 봅니다` };
}

// ----------------------------------------------------------------------
// 표시
// ----------------------------------------------------------------------

// 한도 판정은 web/lib/limits.ts 가 단일 정의처다. 한도를 **쓰는** 쪽(장중 경로)도 같은 식을
// 써야 하는데 이 파일에는 `INSERT INTO health_alerts` 가 있어 그쪽 쓰기 검사에 걸린다.
// 화면은 예전처럼 여기서 가져다 쓴다
export { usageAmount, usageLabel, usageRatio, usageTone } from "@/lib/limits";

export const STATUS_LABEL: Record<string, string> = {
  running: "실행 중", success: "성공", partial: "일부 성공", failed: "실패", skipped: "건너뜀",
  dryrun: "시험 실행",
};

/**
 * 끝나지 않은 채 오래 남은 실행. 워크플로가 중간에 죽으면 행이 'running' 으로 굳는다.
 * 화면이 "실행 중" 으로 보여 주면 사람이 기다리게 되므로 따로 표시한다.
 * 6시간은 가장 긴 배치(미국 전종목 수집 약 17분)보다 넉넉히 잡은 값이다 [확인필요]
 */
export const STUCK_AFTER_HOURS = 6;

export function isStuck(status: string, startedAt: string, now: Date): boolean {
  if (status !== "running") return false;
  return now.getTime() - Date.parse(startedAt) > STUCK_AFTER_HOURS * 3_600_000;
}
