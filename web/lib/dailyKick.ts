/**
 * 아예 안 깨워진 일일 배치를 무응답 감시가 한 번 깨운다 (docs/health.md 5장 예외, docs/infra.md 25.2310, docs/roadmap.md 1장 #5).
 *
 * 왜: 2026-11-23 국내 일일 배치는 정시 깨움(cron-job.org → repository_dispatch) 한 번이 오지 않았고, Actions 예비 예약(09:35 KST)은
 * 3시간 16분 늦게 왔다. 실전이면 개장 전 주문이 한낮 주문이 된다. 무응답 감시(매시, 웹 경로)는 그 사실을 알기만 했다.
 *
 * **좁게 한다** — docs/health.md 5장 "자동 재실행은 하지 않는다" 의 까닭(왜 멈췄는지 모르는 채 다시 돌리면 같은 실패를 반복하거나
 * 한도를 태운다)을 지킨다:
 * - 그 작업의 **실행 기록이 성공 창 안에 한 줄도 없을 때만**(아예 안 깨워짐). 실패·건너뜀·도는 중 기록이 있으면 깨우지 않는다 —
 *   돌았는데 안 된 것은 사람이 본다
 * - 하루 한 번 — 그날의 `missing` 알림을 처음 만드는 호출에서만(같은 날 알림은 UNIQUE 로 하나뿐이라 다음 호출의 `due` 에 다시
 *   오지 않는다). 겹친 두 호출이 둘 다 깨워도 워크플로 동시 실행 그룹(`daily-kr`·`daily-us`)이 줄 세우고 뒤 것은 그날 성공
 *   기록을 보고 곧 끝난다
 * - `--force` 없이 깨운다 — 배치가 제 예약 판단(너무 이르다·이미 장이 끝났다·늦은 리포트)을 그대로 한다. 화면 수동 실행
 *   (`JOBS` 의 `force: true`)과 다르다
 * - 일일 배치 둘만(감성 채점은 아니다 — 그날 판단을 망치지 않는다)
 *
 * 깨운 결과는 그 알림의 글과 `data.kick` 에 남는다(텔레그램 [무응답] 에 "한 번 다시 깨웠습니다" 가 붙는다).
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
/** 그 작업의 실행 기록이 이 시각 뒤로 한 줄이라도 있나 — 색인 (job_name, started_at DESC) 를 탄다 */
export const ANY_RUN_SINCE_SQL =
  "SELECT 1 FROM batch_runs WHERE job_name = ? AND started_at >= ? LIMIT 1";
/** GitHub 호출 상한 — 무응답 경로 전체 상한(30초) 안에서 발송 시간을 남긴다(장중 감시 깨움과 같은 값) */
export const DAILY_KICK_TIMEOUT_MS = 5_000;

export type DailyKick = "kicked" | "ran" | `failed:${string}`;

function 상한fetch(timeoutMs: number): typeof fetch {
  return (input, init) =>
    fetch(input, { ...init, signal: AbortSignal.timeout(timeoutMs) });
}

/**
 * 새로 만들 무응답 알림(`due`)을 받아, 일일 배치의 `missing` 이고 그날 실행 기록이 아예 없으면 한 번 깨운다.
 * 돌려주는 것은 글·`data.kick` 을 덧붙인 알림(나머지는 그대로). 창의 시작은 알림이 이미 싣는 `data.success_window_from`
 * (`successWindowFrom` — 감시가 "오늘 것" 으로 치는 창)이다. 그 칸이 없으면 깨우지 않는다
 */
export async function kickMissingDaily(
  due: Due[],
  dispatch: (
    job: DispatchJob,
  ) => Promise<{ dispatched: boolean; reason?: string }> = (job) =>
    dispatchJob(job, 상한fetch(DAILY_KICK_TIMEOUT_MS)),
): Promise<Due[]> {
  const out: Due[] = [];
  for (const d of due) {
    const job = DAILY_KICK_JOBS[d.job];
    const 창 = d.data.success_window_from;
    const from =
      d.kind === "missing" && job && typeof 창 === "string" && 창 ? 창 : null;
    if (!job || from === null) {
      out.push(d);
      continue;
    }
    let 결과: DailyKick;
    try {
      const 있음 = await execute(ANY_RUN_SINCE_SQL, [d.job, from]);
      if (있음.rows.length) {
        결과 = "ran";
      } else {
        const r = await dispatch(job);
        결과 = r.dispatched
          ? "kicked"
          : `failed:${(r.reason ?? "").slice(0, 80)}`;
      }
    } catch (e: unknown) {
      결과 = `failed:${e instanceof Error ? e.message.slice(0, 80) : "?"}`;
    }
    out.push({
      ...d,
      message: d.message + kickNote(결과),
      data: { ...d.data, kick: 결과 },
    });
  }
  return out;
}

/** 알림 글 끝에 붙는 한 줄 */
export function kickNote(결과: DailyKick): string {
  if (결과 === "kicked")
    return "\n웹 감시가 일일 배치를 한 번 다시 깨웠습니다(아예 안 깨워진 날 — 예약 판단은 배치가 그대로 합니다)";
  if (결과 === "ran")
    return "\n오늘 실행 기록은 있습니다(실패·건너뜀·도는 중) — 다시 깨우지 않았습니다. 까닭을 확인하세요";
  return `\n다시 깨우지 못했습니다: ${결과.slice("failed:".length)}`;
}
