/**
 * Turso 가 풀렸는지 수시로 보고, 풀렸으면 복귀 워크플로를 깨운다 (docs/infra.md 25.12).
 *
 * **왜 날짜를 박지 않나.** 리셋 날짜를 모른다 `[확인필요: 결제 주기]`. "10월 1일" 에만 보면 날짜가
 * 다를 때 놓치거나 한참 늦는다(사용자 지적 2026-09-18). 그래서 이미 도는 크론이 수시로 본다:
 * 미국 뉴스 크론과 무응답 감시가 매시(뉴스는 2026-10-02 까지 1분 크론이 10분에 한 번 봤다, 25.880).
 *
 * **왜 Actions 로 폴링하지 않나.** 짧은 작업도 1분으로 계산된다(CLAUDE.md 비용 규칙). 웹 크론은
 * cron-job.org 가 이미 부르고 있어 더 드는 것이 없다. Actions 는 풀린 것을 본 뒤 **한 번만** 깨운다.
 *
 * 순서: auto 인가 → 복귀 표시가 없는가 → Turso 가 살아 있는가 → 쿨다운 밖이면 깨우고 알린다
 * (깨웠으면 110분, 깨우지 못했으면 12시간 — 같은 알림이 되풀이되지 않게. 110분은 25.467).
 * 옮기기·복귀 표시는 워크플로(scripts/turso_return.py)가 한다. 웹은 계산하지 않는다. 깨우기만 한다.
 */

import { dbBackend, execute, probeTurso, readReturnMarker, rowsToObjects } from "@/lib/db";
import { dispatchJob, findJob, type DispatchResult } from "@/lib/dispatch";
import { sendTelegram } from "@/lib/telegram";

/**
 * 깨운 뒤 이만큼은 다시 깨우지 않는다. 복귀 워크플로는 5~10분 걸리지만 **D1 따라잡기와 같은 줄에 서서**(25.463)
 * 따라잡기가 끝날 때까지 기다릴 수 있다 — 따라잡기 제한 60분 + 복귀 제한 40분 + 여유 10분. 30분이던 때는 기다리는 중에
 * 두 번째 깨우기가 나가 "워크플로가 시작조차 못 한다(Billing 확인)" 거짓 경보를 보냈다 (docs/infra.md 25.467, 교차검증).
 * `__tests__/tursoWatch.test.ts` 가 두 워크플로의 timeout-minutes 합보다 긴지 본다
 */
export const COOLDOWN_MS = 110 * 60_000;
/**
 * 깨우지 못한 뒤(토큰이 없거나 권한이 모자람)는 반나절에 한 번만 다시 해 본다. 같은 알림이 30분마다
 * 가면 안 된다. 그 사이에는 복귀 워크플로의 하루 한 번 예약 실행(09:20 KST)이 대신 돌아간다
 */
export const FAILED_COOLDOWN_MS = 12 * 3_600_000;
/**
 * 이만큼 깨웠는데도 복귀 표시가 안 생기면 **워크플로가 아예 못 도는 것**으로 보고 간격을 늘린다.
 *
 * 왜 필요한가 (2026-09-21, docs/infra.md 25.79): 30분 쿨다운은 "워크플로가 5~10분 걸린다" 는
 * 가정 위에 있다. 그런데 **잡이 시작조차 못 하면**(Actions 무료 분 소진 — 25.69) 복귀 표시가
 * 영영 안 생기고, 같은 알림이 **하루 48번** 간다. 그러면 사람은 알림을 끄고, 진짜로 돌아간
 * 날에도 모른다. 25.58(스스로 낫지 않는 고리)·25.77(거짓 경보)과 같은 모양이다.
 */
export const REPEAT_BACKOFF_AFTER = 2;
/** 마지막으로 깨운 기록. D1 settings 에 둔다 (복귀 표시와 같은 곳) */
export const REQUESTED_KEY = "db_return_requested_at";

/** 깨운 기록. ok 가 null 이면 깨우는 중이다(결과를 적기 전) */
export interface Requested {
  at: string;
  ok: boolean | null;
  /** 복귀 표시가 생기기 전까지 몇 번째 깨움인가. 옛 기록에는 없어 0 으로 본다 */
  tries?: number;
}

export type WatchOutcome =
  | "not-auto" // auto 가 아니다. 사람이 정한 대로 쓴다
  | "returned" // 이미 돌아갔다
  | "unknown" // D1 을 못 읽었다. 옮길 수도 없으니 다음에 본다
  | "blocked" // 아직 한도로 막혀 있다
  | "down" // 한도가 아닌 이유로 응답이 없다. 풀린 것이 아니다
  | "cooldown" // 풀렸고 방금 깨웠다. 워크플로가 도는 중이다
  | "dispatched"
  | "dispatch-failed";

export interface WatchDeps {
  mode: () => "turso" | "d1" | "auto";
  readMarker: () => Promise<boolean | null>;
  probe: () => Promise<{ ok: boolean; blocked: boolean }>;
  lastRequested: () => Promise<Requested | null>;
  markRequested: (request: Requested) => Promise<void>;
  dispatch: () => Promise<DispatchResult>;
  notify: (text: string) => Promise<void>;
  now: () => Date;
}

export async function watchTursoReturn(deps: WatchDeps = defaultDeps()): Promise<WatchOutcome> {
  if (deps.mode() !== "auto") return "not-auto";
  const returned = await deps.readMarker();
  if (returned === true) return "returned";
  if (returned === null) return "unknown";

  const state = await deps.probe();
  if (!state.ok) return state.blocked ? "blocked" : "down";

  const now = deps.now();
  const at = now.toISOString();
  const last = await deps.lastRequested().catch(() => null);
  const 시도 = (last?.tries ?? 0) + 1;
  if (last) {
    // 깨우지 못했거나, 여러 번 깨웠는데도 복귀 표시가 없으면 **간격을 늘린다** (25.79)
    const 오래걸림 = (last.tries ?? 0) >= REPEAT_BACKOFF_AFTER;
    const wait = last.ok === false || 오래걸림 ? FAILED_COOLDOWN_MS : COOLDOWN_MS;
    if (now.getTime() - Date.parse(last.at) < wait) return "cooldown";
  }
  // 깨우기 전에 적는다. 적기가 실패해도(D1 쓰기 한도) 깨운다 — 워크플로는 여러 번 돌아도 한 번만 옮긴다
  await deps.markRequested({ at, ok: null, tries: 시도 }).catch(() => undefined);

  const result = await deps.dispatch();
  await deps.markRequested({ at, ok: result.dispatched, tries: 시도 }).catch(() => undefined);
  const text = !result.dispatched
    ? `🔓 Turso 가 풀렸는데 복귀 작업을 깨우지 못했습니다\n${result.reason ?? "이유를 알 수 없습니다"}\n` +
      "GitHub → Actions → \"Turso 자동 복귀\" 를 직접 실행하세요. 안 해도 하루 한 번(09:20) 예약 실행이 돌아간다"
    : 시도 === 1
      ? "🔓 Turso 가 풀렸습니다. 자동 복귀를 시작합니다\n" +
        "표 → D1 에서 넣은 매매·관심종목·설정 → 복귀 표시 순서로 옮깁니다. 끝나면 \"✅ Turso 로 돌아왔습니다\" 가 옵니다"
      // **두 번째부터는 다른 말을 한다.** 깨우기는 성공했는데 복귀 표시가 안 생겼다는 뜻이고,
      // 가장 흔한 원인은 **잡이 시작조차 못 하는 것**이다(Actions 무료 분 소진, 25.69).
      // 같은 문구를 되풀이하면 "시작합니다" 가 거짓말이 된다
      : `🔓 Turso 는 풀렸는데 복귀가 아직 끝나지 않았습니다 (${시도}번째 깨움)\n` +
        "깨우기는 성공했으니 **워크플로가 시작조차 못 하는 것**일 수 있습니다 — " +
        "GitHub → Settings → Billing 에서 Actions 무료 분이 남았는지 보세요 (docs/infra.md 25.69)";
  // 고지는 `sendTelegram` 이 붙인다 (25.115). 여기서 또 적으면 사본이 하나 더 생긴다
  await deps.notify(text).catch(() => undefined);
  return result.dispatched ? "dispatched" : "dispatch-failed";
}

/** 인스턴스 안의 마지막 깨운 기록. D1 에 적지 못한 날(쓰기 한도)에도 쿨다운을 지키게 한다 */
let lastLocal: Requested | null = null;

/**
 * 저장된 값을 읽는다. 깨진 값은 없는 것으로 본다.
 *
 * **`tries` 를 잃으면 안 된다** (2026-09-22, docs/infra.md 25.134). `markRequested` 는
 * `JSON.stringify(request)` 로 통째로 적는데 여기서는 `at` 과 `ok` 만 되살리고 있었다.
 * 그러면 인스턴스가 바뀔 때마다 `tries` 가 0 으로 되돌아가, 25.79 가 넣은 백오프
 * (`REPEAT_BACKOFF_AFTER` 번 깨웠는데도 복귀 표시가 없으면 간격을 12시간으로)가
 * **한 번도 걸리지 않는다.** 30분마다 깨우고 30분마다 같은 알림을 보낸다.
 *
 * 옛 기록에는 `tries` 가 없다. 그때는 `undefined` 로 두고 부르는 쪽이 0 으로 본다.
 */
export function parseRequested(value: string | null | undefined): Requested | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<Requested>;
    if (typeof parsed?.at !== "string") return null;
    const tries = typeof parsed.tries === "number" && Number.isFinite(parsed.tries) ? parsed.tries : undefined;
    return { at: parsed.at, ok: parsed.ok ?? null, ...(tries === undefined ? {} : { tries }) };
  } catch {
    return null;
  }
}

function defaultDeps(): WatchDeps {
  return {
    mode: dbBackend,
    readMarker: readReturnMarker,
    probe: probeTurso,
    lastRequested: async () => {
      const rows = rowsToObjects<{ value: string }>(
        await execute("SELECT value FROM settings WHERE key = ?", [REQUESTED_KEY]).catch(() => ({
          columns: [],
          rows: [],
          affectedRows: 0,
        })),
      );
      const stored = parseRequested(rows[0]?.value);
      // 둘 중 늦은 것. 같은 시각이면 결과가 적힌 쪽(인스턴스 쪽이 더 최신일 수 있다)
      const candidates = [stored, lastLocal].filter((v): v is Requested => v !== null);
      candidates.sort((a, b) => a.at.localeCompare(b.at) || (a.ok === null ? -1 : 1));
      return candidates.at(-1) ?? null;
    },
    markRequested: async (request) => {
      lastLocal = request;
      await execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)" +
          " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        [REQUESTED_KEY, JSON.stringify(request), request.at],
      );
    },
    dispatch: async () => {
      const job = findJob("turso_return");
      return job ? dispatchJob(job) : { dispatched: false, reason: "turso_return 작업이 목록에 없습니다" };
    },
    notify: (text) => sendTelegram(text),
    now: () => new Date(),
  };
}
