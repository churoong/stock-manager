/**
 * KIS모의투자 장중 손절·익절 감시가 멈췄으면 다시 깨운다 (docs/paper.md 15.11, docs/infra.md 25.1806·25.1807).
 *
 * 왜: 감시는 한 잡이 장 끝까지 10분마다 돈다(25.1796). 시작은 국내 일일 배치의 `guard_start` 잡이 하고, 예비로 kis-guard 의
 * Actions 예약 넷을 두었는데 2026-10-26 하루 내내 **한 번도 오지 않았다**. 일일 배치가 cron-job.org 에서 안 불리거나
 * `guard_start` 가 실패하면 그날 감시가 통째로 빠진다. 웹 장중 경로(`/api/cron/intraday?market=KR`)는 cron-job.org 가 정규장 동안
 * 5분마다 부른다 — 감시 실행 기록이 한동안 없으면 `repository_dispatch(kis-guard)` 로 깨운다(반복 실행이 된다). 화면의 수동
 * 실행과 같은 토큰.
 *
 * **장중 감시 스위치(`INTRADAY_ENABLED`, 25.1026 — DB 소모로 끔)와 따로 돈다**(25.1807, 25.1806 교차검증 [필수]): 스위치 뒤에
 * 두면 꺼진 동안 한 번도 안 돈다. 끈 까닭이 DB 읽기라, 시계로 먼저 거른다 — 평일 KST 09:20~16:10 밖이면 DB 를 읽지 않고,
 * 안이면 색인 타는 한 행짜리 읽기 둘(오늘 장·최근 감시 기록)뿐이다. 쓰기는 하루 한 번 흔적 한 줄과 깨울 때 설정 한 줄.
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
/** GitHub 호출 상한 — 장중 경로 전체 상한(30초) 안에서 본업 시간을 남긴다(25.1807, 교차검증 [권고]) */
export const GUARD_DISPATCH_TIMEOUT_MS = 5_000;
/** DB 를 읽기 전 시계 거름(KST 분) — 정규장 앞뒤를 넉넉히. 끝은 수능일(10:00~16:30 개장, 깨우기 창 ~16:05)까지 덮는다
 * (25.1808, 25.1807 교차검증 [권고] — 15:10 이면 수능일 오후가 비었다). 보통 날 15:05 뒤는 아래 세션 창이 거른다 */
export const GUARD_CLOCK_FROM_KST_MIN = 9 * 60 + 20;
export const GUARD_CLOCK_TO_KST_MIN = 16 * 60 + 10;
export const GUARD_KICK_KEY = "kis_guard_kick_at";
export const GUARD_RECENT_SQL = "SELECT 1 FROM batch_runs WHERE job_name = 'intraday_guard' AND started_at >= ? LIMIT 1";
export const GUARD_SESSION_SQL = "SELECT open_utc, close_utc FROM market_sessions WHERE market = 'KR' AND date = ?";
/** 하루 한 번 "이 경로가 그날 장중에 불려 감시를 봤다" 흔적(25.1808) — 깨울 일이 없어 막기 행이 빈 날과 경로가 아예 안
 * 불린 날을 가른다. 값이 같으면 쓰지 않는다(그날 첫 호출 한 번만 한 줄) */
export const GUARD_SEEN_KEY = "kis_guard_seen";
export const GUARD_SEEN_SQL = `INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at WHERE settings.value <> excluded.value`;
/** 깨운 결과를 같은 행에 — 장중 감시가 꺼져 호출 기록이 남지 않는 동안 운영에서 볼 흔적 */
export const GUARD_KICK_RESULT_SQL = "UPDATE settings SET value = ? WHERE key = ?";

export const GUARD_JOB: DispatchJob = {
  key: "kis_guard",
  label: "KIS모의투자 장중 손절·익절",
  event: "kis-guard",
  note: "장 끝까지 10분마다 — 웹 장중 경로가 멈춘 감시를 깨운다",
  run: { name: "intraday_guard", market: "KR" },
};

/** KST (날짜, 분, 평일인가) */
function kst(now: Date): { date: string; minute: number; weekday: boolean } {
  const k = new Date(now.getTime() + 9 * 3_600_000);
  const d = k.getUTCDay();
  return { date: k.toISOString().slice(0, 10), minute: k.getUTCHours() * 60 + k.getUTCMinutes(), weekday: d >= 1 && d <= 5 };
}

/** DB 를 읽기 전 거름 — 평일 KST 09:20~15:10 */
export function clockMayKick(now: Date): boolean {
  const k = kst(now);
  return k.weekday && k.minute >= GUARD_CLOCK_FROM_KST_MIN && k.minute <= GUARD_CLOCK_TO_KST_MIN;
}

/** 지금 볼 때인가 — 그날 세션의 개장 + 25분 ~ 마감 − 25분 */
export function kickWindow(session: { open_utc: string; close_utc: string }, now: Date): boolean {
  const 열림 = Date.parse(session.open_utc);
  const 닫힘 = Date.parse(session.close_utc);
  if (!Number.isFinite(열림) || !Number.isFinite(닫힘)) return false;
  const t = now.getTime();
  return t >= 열림 + GUARD_KICK_AFTER_OPEN_MS && t <= 닫힘 - GUARD_KICK_BEFORE_CLOSE_MS;
}

export type KickResult = "off_clock" | "no_session" | "not_window" | "alive" | "throttled" | "kicked" | `failed:${string}`;

function 상한fetch(timeoutMs: number): typeof fetch {
  return (input, init) => fetch(input, { ...init, signal: AbortSignal.timeout(timeoutMs) });
}

/** 국내 장중 호출에서 부른다. **던지지 않는다** — DB·GitHub 실패도 `failed:` 글로(장중 경로 본업을 막지 않게) */
export async function kickGuardIfSilent(
  now: Date,
  dispatch: (job: DispatchJob) => Promise<{ dispatched: boolean; reason?: string }> = (job) =>
    dispatchJob(job, 상한fetch(GUARD_DISPATCH_TIMEOUT_MS)),
): Promise<KickResult> {
  if (!clockMayKick(now)) return "off_clock";
  try {
    const 세션 = (await execute(GUARD_SESSION_SQL, [kst(now).date])).rows[0];
    if (!세션) return "no_session";
    if (!kickWindow({ open_utc: String(세션[0]), close_utc: String(세션[1]) }, now)) return "not_window";
    await execute(GUARD_SEEN_SQL, [GUARD_SEEN_KEY, JSON.stringify(kst(now).date), now.toISOString()]);
    const 최근 = await execute(GUARD_RECENT_SQL, [new Date(now.getTime() - GUARD_SILENT_MS).toISOString()]);
    if (최근.rows.length) return "alive";
    const 막기 = await execute(LIVE_THROTTLE_SQL, [
      GUARD_KICK_KEY, JSON.stringify({ at: now.toISOString() }), now.toISOString(), new Date(now.getTime() - GUARD_KICK_GAP_MS).toISOString(),
    ]);
    if (막기.affectedRows === 0) return "throttled";
    const r = await dispatch(GUARD_JOB);
    const 결과: KickResult = r.dispatched ? "kicked" : `failed:${(r.reason ?? "").slice(0, 80)}`;
    await execute(GUARD_KICK_RESULT_SQL, [JSON.stringify({ at: now.toISOString(), result: 결과 }), GUARD_KICK_KEY]).catch(() => undefined);
    return 결과;
  } catch (e: unknown) {
    return `failed:${e instanceof Error ? e.message.slice(0, 80) : "?"}`;
  }
}
