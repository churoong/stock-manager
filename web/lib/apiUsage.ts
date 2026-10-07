/**
 * 웹이 부른 바깥 API 를 **한도 카운터에 센다** (CLAUDE.md 비용 규칙, docs/infra.md 25.105).
 *
 * 2026-09-22 까지 `api_usage` 에 쓰는 곳은 **배치뿐**이었다. 그런데 장중 경로
 * (`/api/cron/intraday`)가 보유 종목마다 DART 공시를 직접 불렀고, 그 호출은 **어디에도
 * 세지지 않았다.** 결과가 둘이다.
 *
 *   1. `/status` 의 DART 게이지가 실제보다 **적게** 보인다. 80% 경고가 늦거나 안 온다
 *   2. 배치가 "아직 여유 있다" 고 판단하고 계속 부른다 — 여유는 이미 웹이 썼다
 *
 * 세는 자리가 갈리면 한도 판정이 틀린다는 것은 25.68 에서 한 번 겪었다(DART 를 두 이름으로
 * 세고 있었다). 이번에는 **이름이 아니라 언어가 갈렸다.**
 *
 * **쓰기를 아낀다.** 호출마다 한 줄씩 쓰면 5분 크론이 하루 수백 번 쓴다. 한 번 부를 때
 * 실제로 나간 횟수를 세어 **한 번만** 더한다.
 */

import { execute } from "@/lib/db";
import { usageRatio, usageTone } from "@/lib/limits";

/**
 * `api_usage.api_name` 은 **배치와 글자까지 같아야 한다.**
 * 정의처는 `batch/core/db.API_NAMES` 이고, `tests/test_api_usage_names.py` 가 대조한다.
 */
export const DART_API_NAME = "dart_opendart";

/**
 * DART 일일 한도. 오류코드 `020` 의 설명에 근거한다 (docs/data-sources.md).
 * 공식 문서가 "요청 제한이 다르게 설정될 수 있다" 고 적어 두었으므로 고정 보장값은 아니다.
 */
export const DART_DAILY_LIMIT = 20_000;

export interface Usage {
  call_count: number;
  limit_value: number | null;
  warn_at_pct: number;
  /** 지난번에 적어 둔 판정. 바깥이 "제한 초과" 라고 답했다면 셈이 모자라도 여기에 남는다 */
  state: string;
}

export const USAGE_TODAY = `SELECT call_count, limit_value, warn_at_pct, state FROM api_usage
WHERE api_name = ? AND window_type = 'day' AND window_start = ?`;

/**
 * 횟수를 더하고 상태를 다시 적는다.
 *
 * 상태를 SQL 안에서 계산하지 않는다 — 그러면 80/100 문턱이 세 벌째가 된다
 * (파이썬 · `lib/limits.ts` · SQL). 읽은 값에 더할 만큼을 더해 여기서 판정한다.
 * 그사이 배치가 끼어들면 잠깐 어긋나지만 **다음 호출이 실제 값으로 다시 적는다.**
 */
export const USAGE_ADD = `INSERT INTO api_usage
  (api_name, window_type, window_start, call_count, limit_value, warn_at_pct, state, last_call_at, updated_at)
VALUES (?, 'day', ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (api_name, window_type, window_start) DO UPDATE SET
  call_count = api_usage.call_count + excluded.call_count,
  limit_value = COALESCE(excluded.limit_value, api_usage.limit_value),
  state = excluded.state,
  last_call_at = excluded.last_call_at,
  updated_at = excluded.updated_at`;

/** 그 API 의 **오늘** 사용량. 행이 없으면 0 에서 시작한다 */
export async function readUsage(apiName: string, now: Date, limitValue: number | null = null): Promise<Usage> {
  const day = now.toISOString().slice(0, 10);
  const rs = await execute(USAGE_TODAY, [apiName, day]);
  const row = rs.rows[0];
  if (!row) return { call_count: 0, limit_value: limitValue, warn_at_pct: 80, state: "unknown" };
  return {
    call_count: Number(row[0] ?? 0),
    limit_value: row[1] === null || row[1] === undefined ? limitValue : Number(row[1]),
    warn_at_pct: Number(row[2] ?? 80),
    state: String(row[3] ?? "unknown"),
  };
}

/**
 * 한도를 다 썼나.
 *
 * **적어 둔 판정을 먼저 본다.** 셈으로만 보면, 바깥이 "제한 초과" 라고 답해서 `blocked`
 * 로 적어 둔 것이 다음 호출에서 그대로 무시된다 — 적어 놓고 읽을 때 안 거는 셈이다
 * (25.104 에서 본 모양). 계정마다 한도가 다를 수 있어 우리 셈은 늘 한도에 못 미친다.
 *
 * **모르면 막지 않는다** — 모르는 것을 나쁘다고 단정하지 않는다(25.0).
 *
 * 남은 한계: 배치(`batch/core/db.record_api_call`)는 이 열을 **셈으로만** 다시 쓴다.
 * 같은 날 배치가 DART 를 부르면 여기 적은 `blocked` 가 지워진다. 배치는 장 열리기 전에
 * 한 번 돌고 이 경로는 장중에 도므로 실제로 겹치지는 않는다 `[확인필요: 따라잡기가 장중에 돌 때]`.
 */
export function isBlocked(usage: Usage): boolean {
  if (usage.state === "blocked") return true;
  return usageTone(usageRatio(usage.call_count, usage.limit_value), usage.warn_at_pct) === "blocked";
}

/**
 * 실제로 나간 호출 수를 더한다. **실패해도 본 작업을 막지 않는다** (호출 기록과 같은 이유).
 *
 * `blocked` 는 바깥 API 가 스스로 "제한 초과" 라고 답했을 때다(DART `020`·`021`).
 * 그때는 우리 셈이 한도에 못 미쳐도 **막힌 것이 사실**이므로 그대로 적는다.
 */
export async function addUsage(
  apiName: string,
  count: number,
  now: Date,
  limitValue: number | null,
  opts: { blocked?: boolean } = {},
): Promise<void> {
  if (count <= 0 && !opts.blocked) return;
  const day = now.toISOString().slice(0, 10);
  const before = await readUsage(apiName, now, limitValue);
  const limit = before.limit_value ?? limitValue;
  const state = opts.blocked
    ? "blocked"
    : usageTone(usageRatio(before.call_count + count, limit), before.warn_at_pct);
  const stamp = now.toISOString();
  await execute(USAGE_ADD, [apiName, day, count, limitValue, before.warn_at_pct, state, stamp, stamp]);
}
