/**
 * 아예 안 깨워진 일일 배치를 무응답 감시가 한 번 깨운다 (docs/health.md 5장 예외, docs/infra.md 25.2310, docs/roadmap.md 1장 #5).
 *
 * 왜: 2026-11-23 국내 일일 배치는 정시 깨움(cron-job.org → repository_dispatch) 한 번이 오지 않았고, Actions 예비 예약(09:35 KST)은
 * 3시간 16분 늦게 왔다. 실전이면 개장 전 주문이 한낮 주문이 된다. 무응답 감시(매시, 웹 경로)는 그 사실을 알기만 했다.
 *
 * **좁게 한다** — docs/health.md 5장 "자동 재실행은 하지 않는다" 의 까닭(왜 멈췄는지 모르는 채 다시 돌리면 같은 실패를 반복하거나
 * 한도를 태운다)을 지킨다:
 * - 그 작업이 **이 세션을 위해 시도한 실행이 아예 없을 때만**(아예 안 깨워짐 — 그 시장 오늘 자정 뒤, 예정된 건너뜀·확인 실행 빼고,
 *   25.2311). 실패·건너뜀(예정 밖)·도는 중 기록이 있으면 깨우지 않는다 — 돌았는데 안 된 것은 사람이 본다
 * - 하루 한 번 — 알림을 **저장한 뒤** 그 행을 조건부 UPDATE 로 잡은 호출만 깨운다(25.2311). 저장이 깨지면 깨우지 않는다(다음 매시
 *   호출이 같은 알림을 다시 만들어 매시 깨우던 길을 막는다). 겹친 두 호출도 하나만 잡는다
 * - `--force` 없이 깨운다 — 배치가 제 예약 판단(너무 이르다·이미 장이 끝났다·늦은 리포트)을 그대로 한다. 화면 수동 실행
 *   (`JOBS` 의 `force: true`)과 다르다
 * - 일일 배치 둘만(감성 채점은 아니다 — 그날 판단을 망치지 않는다)
 *
 * 깨운 결과는 그 알림 행의 글 끝과 `data.kick`(`kicked`·`ran:<상태>`·`failed:…`)에 남는다 — 발송 전에 고쳐 적는다.
 * **던지지 않는다** — DB·GitHub 실패도 `failed:` 글로(알림 발송을 막지 않게).
 */
import { execute } from "@/lib/db";
import { type DispatchJob, dispatchJob } from "@/lib/dispatch";
import type { Due } from "@/lib/health";

/** 깨우는 작업 — `force` 없음(예약 판단 그대로). 이벤트 이름은 워크플로 `repository_dispatch.types` 와 같아야 한다(시험이 대조) */
export const DAILY_KICK_JOBS: Record<string, DispatchJob> = {
  daily_kr: {
    key: "daily_kr",
    label: "국내 일일 배치",
    event: "daily-kr",
    note: "무응답 감시가 한 번 다시 깨움(예약 판단 그대로)",
  },
  daily_us: {
    key: "daily_us",
    label: "미국 일일 배치",
    event: "daily-us",
    note: "무응답 감시가 한 번 다시 깨움(예약 판단 그대로)",
  },
};
/**
 * 그 작업이 **이 세션을 위해 실제로 시도한** 실행이 있나 (25.2311, 25.2310 교차검증 [필수] 2).
 * - 창의 시작 = 그 시장 오늘 날짜의 자정(`marketMidnightUtc`) — 성공 창(개장 − 12시간)을 그대로 쓰면 전날 밤 손 실행·전날 늦은 예비의
 *   건너뜀 줄이 "오늘 돌았다" 로 잡혀 깨우지 않고, 알림 글은 "오늘 실행 기록은 있습니다" 라고 틀렸다(25.376 이 성공 판정에서 막은 함정)
 * - **예정된 건너뜀**("아직 이릅니다"·"휴장일" — 주간 요약 `_예정된_건너뜀` 과 같은 글)은 시도가 아니다 — 겨울(EST) 미국은 Actions
 *   12:27 UTC 예약이 매일 "개장까지 123분 남아 아직 이릅니다" 로 건너뛰어, 그 줄 때문에 겨울 내내 미국 깨움이 꺼졌을 것이다
 * - 확인 실행(`dryrun`)도 시도가 아니다
 * 깨진 step_log 한 줄이 질의를 깨지 않게 `CASE WHEN json_valid` 로 감싼다
 */
export const ANY_RUN_SINCE_SQL = `SELECT status FROM batch_runs WHERE job_name = ? AND started_at >= ? AND status <> 'dryrun'
AND NOT (status = 'skipped' AND (COALESCE(CASE WHEN json_valid(step_log) THEN json_extract(step_log, '$.decision') END, '') LIKE '%아직 이릅니다%'
  OR COALESCE(CASE WHEN json_valid(step_log) THEN json_extract(step_log, '$.decision') END, '') LIKE '%휴장일%'))
LIMIT 1`;
/**
 * 알림을 **먼저 저장한 뒤** 그 행을 잡는다(25.2311, 25.2310 교차검증 [권고]) — 깨움이 저장보다 앞서면 저장이 깨진 날(쓰기 한도·일시
 * 오류) 다음 매시 호출이 같은 [무응답] 을 다시 만들고 또 깨웠다. 겹친 두 호출도 이 조건부 UPDATE 로 하나만 잡는다
 */
export const KICK_CLAIM_SQL = `UPDATE health_alerts SET data = json_set(data, '$.kick', 'claim')
WHERE job = ? AND market = ? AND local_date = ? AND kind = 'missing' AND json_extract(data, '$.kick') IS NULL AND sent_at IS NULL`;
export const KICK_RESULT_SQL = `UPDATE health_alerts SET message = message || ?, data = json_set(data, '$.kick', ?)
WHERE job = ? AND market = ? AND local_date = ? AND kind = 'missing' AND sent_at IS NULL`;
/** 개장 시각에서 거슬러 그 시장 자정까지(국내 09:00·미국 09:30 개장). 늦게 여는 날(수능일 10:00)은 자정이 그만큼 늦게 잡힌다 —
 * 창이 한 시간 좁아질 뿐 그날 아침 시도는 모두 그 뒤다 */
export const OPEN_AFTER_MIDNIGHT_MIN: Record<string, number> = {
  KR: 9 * 60,
  US: 9 * 60 + 30,
};

export function marketMidnightUtc(
  market: string,
  sessionOpenUtc: string,
): string | null {
  const 열림 = Date.parse(sessionOpenUtc);
  const 분 = OPEN_AFTER_MIDNIGHT_MIN[market];
  if (!Number.isFinite(열림) || 분 === undefined) return null;
  return new Date(열림 - 분 * 60_000).toISOString();
}

/** GitHub 호출 상한 — 무응답 경로 전체 상한(30초) 안에서 발송 시간을 남긴다(장중 감시 깨움과 같은 값) */
export const DAILY_KICK_TIMEOUT_MS = 5_000;

export type DailyKick = "kicked" | `ran:${string}` | `failed:${string}`;

function 상한fetch(timeoutMs: number): typeof fetch {
  return (input, init) =>
    fetch(input, { ...init, signal: AbortSignal.timeout(timeoutMs) });
}

/**
 * **저장된** 무응답 알림(`due` — 방금 저장한 것)을 받아, 일일 배치의 `missing` 이고 그 세션을 위한 시도가 아예 없으면 한 번 깨운다.
 * 먼저 그 알림 행을 잡고(`KICK_CLAIM_SQL` — 못 잡으면 다른 호출 몫), 결과를 그 행의 글 끝·`data.kick` 에 적는다.
 * 돌려주는 것은 각 알림의 깨움 결과(깨울 후보가 아니면 없음). **던지지 않는다**
 */
export async function kickMissingDaily(
  due: Due[],
  dispatch: (
    job: DispatchJob,
  ) => Promise<{ dispatched: boolean; reason?: string }> = (job) =>
    dispatchJob(job, 상한fetch(DAILY_KICK_TIMEOUT_MS)),
): Promise<
  Array<{ job: string; market: string; kick: DailyKick; noted: boolean }>
> {
  const out: Array<{
    job: string;
    market: string;
    kick: DailyKick;
    noted: boolean;
  }> = [];
  for (const d of due) {
    const job = DAILY_KICK_JOBS[d.job];
    const 열림 = d.data.session_open_utc;
    const from =
      d.kind === "missing" && job && typeof 열림 === "string"
        ? marketMidnightUtc(d.market, 열림)
        : null;
    if (!job || from === null) continue;
    const 열쇠 = [d.job, d.market, d.localDate];
    let 결과: DailyKick;
    try {
      const 잡음 = await execute(KICK_CLAIM_SQL, 열쇠);
      if (잡음.affectedRows === 0) continue; // 다른 호출이 잡았거나 저장되지 않았다
      const 있음 = await execute(ANY_RUN_SINCE_SQL, [d.job, from]);
      if (있음.rows.length) {
        결과 = `ran:${String(있음.rows[0][0])}`;
      } else {
        const r = await dispatch(job);
        결과 = r.dispatched
          ? "kicked"
          : `failed:${(r.reason ?? "").slice(0, 80)}`;
      }
    } catch (e: unknown) {
      결과 = `failed:${e instanceof Error ? e.message.slice(0, 80) : "?"}`;
    }
    // 결과를 그 행에 적는다 — 겹친 호출이 그 사이 그 행을 발송용으로 잡았으면(sent_at) 0행이고 [무응답] 글에 결과 줄이 빠진다.
    // 그때는 `noted: false` 로 heartbeat detail 에 남는다(25.2312, 25.2311 교차검증 [권고] — 예전엔 조용히 넘어갔다)
    // 결과 쓰기가 실패해도(쓰기 한도·일시 오류) 던지지 않는다 — 삼키는 것이 아니라 `noted: false` 로 heartbeat 에 남는다
    let 적음 = 0;
    try {
      적음 = (await execute(KICK_RESULT_SQL, [kickNote(결과), 결과, ...열쇠]))
        .affectedRows;
    } catch {
      적음 = 0;
    }
    out.push({
      job: d.job,
      market: d.market,
      kick: 결과,
      noted: 적음 > 0,
    });
  }
  return out;
}

/** 알림 글 끝에 붙는 한 줄 */
export function kickNote(결과: DailyKick): string {
  if (결과 === "kicked")
    return "\n웹 감시가 일일 배치를 한 번 다시 깨웠습니다(오늘 시도가 아예 없던 날 — 예약 판단은 배치가 그대로 합니다)";
  if (결과.startsWith("ran:"))
    return `\n오늘 이 세션을 위한 실행 기록이 있습니다(${결과.slice(4)}) — 다시 깨우지 않았습니다. 까닭을 확인하세요`;
  return `\n다시 깨우지 못했습니다: ${결과.slice("failed:".length)}`;
}
