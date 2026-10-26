/**
 * KIS모의투자 장중 손절·익절 감시가 멈췄으면 다시 깨운다 (docs/paper.md 15.11, docs/infra.md 25.1806).
 *
 * 왜: 감시는 한 잡이 장 끝까지 10분마다 돈다(25.1796). 시작은 국내 일일 배치의 `guard_start` 잡이 하고, 예비로 kis-guard 의
 * Actions 예약 넷을 두었는데 2026-10-26 하루 내내 **한 번도 오지 않았다**. 일일 배치가 cron-job.org 에서 안 불리거나
 * `guard_start` 가 실패하면 그날 감시가 통째로 빠진다. 이 장중 경로는 cron-job.org 가 정규장 동안 5분마다 **정시에** 부른다 —
 * 감시 실행 기록이 한동안 없으면 `repository_dispatch(kis-guard)` 로 깨운다(반복 실행이 된다). 화면의 수동 실행과 같은 토큰.
 *
 * 깨우는 것뿐이다 — 주문은 감시가 낸다. 이미 돌고 있으면 GitHub 동시 실행 그룹 뒤에서 기다렸다가 창이 지나 끝난다(무해).
 */
import { execute } from "@/lib/db";
import { type DispatchJob, dispatchJob } from "@/lib/dispatch";
import { LIVE_THROTTLE_SQL } from "@/lib/kisVts";

/** 감시 회차는 10분마다 실행 기록 한 줄 — 두 회차 + 여유가 비면 멈춘 것으로 본다 */
export const GUARD_SILENT_MS = 25 * 60_000;
/** 개장 뒤 이만큼은 보지 않는다 — 첫 회차(개장 + 5분)와 아침 일일 배치가 깨운 실행이 자리 잡을 시간 */
export const GUARD_KICK_AFTER_OPEN_MS = 25 * 60_000;
/** 마감 이만큼 전부터는 깨우지 않는다 — 감시 창이 마감 − 15분에 닫히고 러너가 뜨는 데 몇 분 걸린다 */
export const GUARD_KICK_BEFORE_CLOSE_MS = 25 * 60_000;
/** 한 번 깨우면 이만큼은 다시 깨우지 않는다(러너 대기·설치 시간) — 설정 행 조건부 upsert 로 원자적 */
export const GUARD_KICK_GAP_MS = 30 * 60_000;
export const GUARD_KICK_KEY = "kis_guard_kick_at";
export const GUARD_RECENT_SQL = "SELECT 1 FROM batch_runs WHERE job_name = 'intraday_guard' AND started_at >= ? LIMIT 1";

export const GUARD_JOB: DispatchJob = {
  key: "kis_guard",
  label: "KIS모의투자 장중 손절·익절",
  event: "kis-guard",
  note: "장 끝까지 10분마다 — 웹 장중 경로가 멈춘 감시를 깨운다",
  run: { name: "intraday_guard", market: "KR" },
};

/** 지금 볼 때인가 — 개장 + 25분 ~ 마감 − 25분 */
export function kickWindow(session: { open_utc: string; close_utc: string }, now: Date): boolean {
  const 열림 = Date.parse(session.open_utc);
  const 닫힘 = Date.parse(session.close_utc);
  if (!Number.isFinite(열림) || !Number.isFinite(닫힘)) return false;
  const t = now.getTime();
  return t >= 열림 + GUARD_KICK_AFTER_OPEN_MS && t <= 닫힘 - GUARD_KICK_BEFORE_CLOSE_MS;
}

export type KickResult = "not_window" | "alive" | "throttled" | "kicked" | `failed:${string}`;

/** 국내 장중 호출에서 부른다. 실패는 던지지 않고 결과 글로 — 장중 알림 본업을 막지 않는다 */
export async function kickGuardIfSilent(
  session: { open_utc: string; close_utc: string },
  now: Date,
  dispatch: (job: DispatchJob) => Promise<{ dispatched: boolean; reason?: string }> = dispatchJob,
): Promise<KickResult> {
  if (!kickWindow(session, now)) return "not_window";
  const 최근 = await execute(GUARD_RECENT_SQL, [new Date(now.getTime() - GUARD_SILENT_MS).toISOString()]);
  if (최근.rows.length) return "alive";
  const 막기 = await execute(LIVE_THROTTLE_SQL, [
    GUARD_KICK_KEY, JSON.stringify(now.toISOString()), now.toISOString(), new Date(now.getTime() - GUARD_KICK_GAP_MS).toISOString(),
  ]);
  if (막기.affectedRows === 0) return "throttled";
  const r = await dispatch(GUARD_JOB);
  return r.dispatched ? "kicked" : `failed:${(r.reason ?? "").slice(0, 80)}`;
}
