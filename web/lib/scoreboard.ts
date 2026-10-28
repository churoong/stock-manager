/**
 * 전략 깔때기 판정판 (docs/funnel.md 2.2, 기록 25.1940) — 배치가 실행 기록에 실은 줄을 글로만 만든다.
 *
 * 읽는 것: 백테스트 실행 기록 한 줄(`batch_runs.step_log`)의 `scoreboard`(배치 `scoreboard.step_log_rows` 가 낸 줄들)와
 * `scoreboard_error`(판정판이 깨진 실행). 화면은 **계산하지 않는다** — 값은 그대로 꼴만 바꾸고(%·소수 자리), 빈 칸은
 * 배치가 적은 까닭(`missing`)을 "없음: 까닭" 으로 붙인다. 3장 올림·내림 문턱은 여기서도 적용하지 않는다(칸만).
 */

import { z } from "zod";
import { mergeWarnings, strategyLabel } from "@/lib/backtest";
import { userTimeOf } from "@/lib/market";
import { pct } from "@/lib/portfolio";

/** 판정판 줄의 칸 — 배치 `services/scoreboard.STEP_LOG_COLS` 와 같아야 한다(`tests/test_scoreboard_wiring_1940.py` 가 대조) */
export const SCOREBOARD_KEYS = [
  "strategy", "market", "cohort", "trials_cumulative", "cagr", "excess_cagr", "mdd", "dsr", "dsr_n",
  "control_lower", "gate_lower", "half_first_cagr", "half_second_cagr", "walk_positive", "walk_total", "months",
  "rebalances", "buys", "control_blocks", "gate_blocks", "sells_scored", "bh_q", "stage01_verdict", "stage01_note",
] as const;

/** 판정판을 싣는 작업 — 배치 `jobs/backtest.JOB_NAME`·`jobs/core_backtest` 의 세 이름(`JOB_NAME`·`EXPLORE_JOB_NAME`·`LIMIT_JOB_NAME`),
 * 모두 네 이름. `tests/test_scoreboard_wiring_1940.py` 가 배치 상수와 대조한다(25.1941) */
export const SCOREBOARD_JOBS = ["backtest", "backtest_core", "backtest_explore", "backtest_limit"] as const;
const JOBS_SQL = SCOREBOARD_JOBS.map((j) => `'${j}'`).join(", ");

/** step_log 이 JSON 이 아닌 옛 행에서 json_extract 가 질의를 죽이지 않게 CASE 로 막는다(CASE 는 앞 조건이 거짓이면 뒤를 안 푼다) */
const 칸 = (path: string) => `CASE WHEN json_valid(step_log) THEN json_extract(step_log, '${path}') END`;

/** 고른 묶음(group_id)을 낸 실행 기록의 판정판 — step_log 전체 대신 두 칸만 가져온다 */
export const SCOREBOARD_OF_GROUP = `SELECT job_name, started_at, ${칸("$.scoreboard")} AS scoreboard,
  ${칸("$.scoreboard_error")} AS scoreboard_error
FROM batch_runs WHERE job_name IN (${JOBS_SQL}) AND market = ? AND ${칸("$.group_id")} = ?
ORDER BY started_at DESC, id DESC LIMIT 1`;

/** 그 시장에서 판정판이 실린 가장 최근 실행 — 고른 묶음의 기록에 판정판이 없을 때(`pickScoreboard`) 대신 보인다 */
export const LATEST_SCOREBOARD = `SELECT job_name, started_at, ${칸("$.scoreboard")} AS scoreboard,
  ${칸("$.scoreboard_error")} AS scoreboard_error
FROM batch_runs WHERE job_name IN (${JOBS_SQL}) AND market = ? AND ${칸("$.scoreboard")} IS NOT NULL
ORDER BY started_at DESC, id DESC LIMIT 1`;

export interface ScoreboardRun {
  job_name: string;
  started_at: string;
  /** json_extract 가 돌려준 JSON 글(배열) */
  scoreboard: string | null;
  scoreboard_error: string | null;
}

const numOrNull = z.number().nullable().catch(null);
const rowSchema = z.object({
  strategy: z.string(),
  market: z.string().nullable().catch(null),
  cohort: z.string().nullable().catch(null),
  trials_cumulative: numOrNull,
  cagr: numOrNull,
  excess_cagr: numOrNull,
  mdd: numOrNull,
  dsr: numOrNull,
  dsr_n: numOrNull,
  control_lower: numOrNull,
  gate_lower: numOrNull,
  half_first_cagr: numOrNull,
  half_second_cagr: numOrNull,
  walk_positive: numOrNull,
  walk_total: numOrNull,
  months: numOrNull,
  bh_q: numOrNull,
  stage01_verdict: z.string().nullable().catch(null),
  stage01_note: z.string().nullable().catch(null),
  rebalances: numOrNull,
  buys: numOrNull,
  control_blocks: numOrNull,
  gate_blocks: numOrNull,
  sells_scored: numOrNull,
  missing: z.record(z.string(), z.string()).catch({}),
});
export type ScoreRow = z.infer<typeof rowSchema>;

/** 판정판 글(JSON 배열) → 줄들. 모양이 틀린 줄은 버리고 그 수를 센다 — 깨진 줄 하나가 화면을 죽이지 않게 */
export function parseScoreboard(raw: unknown): { rows: ScoreRow[]; unreadable: number } {
  let v: unknown = raw;
  if (typeof raw === "string") {
    try {
      v = JSON.parse(raw);
    } catch {
      return { rows: [], unreadable: 1 };
    }
  }
  if (!Array.isArray(v)) return { rows: [], unreadable: v == null ? 0 : 1 };
  const rows: ScoreRow[] = [];
  let unreadable = 0;
  for (const x of v) {
    const p = rowSchema.safeParse(x);
    if (p.success) rows.push(p.data);
    else unreadable += 1;
  }
  return { rows, unreadable };
}

const COL_LABEL: Record<string, string> = {
  market: "시장", cohort: "코호트", trials_cumulative: "누적 시도", cagr: "CAGR", excess_cagr: "초과 CAGR", mdd: "MDD",
  dsr: "DSR", dsr_n: "DSR N", control_lower: "대조 하한", gate_lower: "관문 하한", half_first_cagr: "앞 반",
  half_second_cagr: "뒤 반", walk_positive: "걷는 창", walk_total: "걷는 창 수", months: "개월", bh_q: "BH q", stage01_verdict: "0→1 점검", stage01_note: "0→1 근거", rebalances: "리밸런스", buys: "매수", control_blocks: "대조 묶음",
  gate_blocks: "관문 묶음", sells_scored: "판 뒤 매도",
};

export const JOB_LABEL: Record<string, string> = {
  backtest: "월 백테스트", backtest_core: "핵심 H", backtest_explore: "탐색", backtest_limit: "회차 지정가",
};

const 없음 = "없음";
const f1 = (v: number | null) => (v == null ? 없음 : v.toFixed(1));
const f2 = (v: number | null) => (v == null ? 없음 : v.toFixed(2));
const p = (v: number | null) => (v == null ? 없음 : pct(v));
const n = (v: number | null) => (v == null ? 없음 : String(v));

export interface ScoreLine {
  strategy: string;
  label: string;
  /** 코호트 글 — 없으면 "코호트 밖" (까닭은 missing 줄에) */
  cohort: string;
  cells: Array<[string, string]>;
  /** 표본 한 줄 */
  sample: string;
  /** "없음: 칸·칸 — 까닭" — 같은 까닭은 한 줄로 묶는다 */
  missing: string[];
  /** 0→1(리플레이 → 그림자) 점검 한 줄(25.1950) — 배치가 funnel.md 3장 첫 값에 대어 본 결과. 시도 아닌 줄은 null */
  stage: string | null;
}

/** 걷는 창 칸 글(25.1947) — 창이 하나도 없으면(12개월 미만) "0/0" 대신 그렇게 적는다 */
function walkText(r: Pick<ScoreRow, "walk_positive" | "walk_total">): string {
  if (r.walk_positive == null || r.walk_total == null) return 없음;
  return r.walk_total === 0 ? "창 없음(12개월 미만)" : `${r.walk_positive}/${r.walk_total} 창 초과 > 0`;
}

/** 판정판 한 줄 → 화면 글. 숫자는 꼴만 바꾼다(비율은 %, 분위 하한은 소수 한 자리, DSR 은 두 자리) */
export function scoreLine(r: ScoreRow): ScoreLine {
  const 까닭별 = new Map<string, string[]>();
  for (const k of SCOREBOARD_KEYS) {
    const why = r.missing[k];
    if (why === undefined || k === "strategy") continue;
    까닭별.set(why, [...(까닭별.get(why) ?? []), COL_LABEL[k] ?? k]);
  }
  return {
    strategy: r.strategy,
    label: strategyLabel(r.strategy),
    cohort: r.cohort == null ? "코호트 밖" : `코호트 ${r.cohort}`,
    cells: [
      ["CAGR", p(r.cagr)],
      ["초과 CAGR", p(r.excess_cagr)],
      ["MDD", p(r.mdd)],
      ["DSR", r.dsr == null ? 없음 : `${f2(r.dsr)}${r.dsr_n == null ? "" : ` (N ${r.dsr_n})`}`],
      ["대조 하한", f1(r.control_lower)],
      // 코호트 BH q(25.1951) — 같은 코호트 시도 줄들의 대조 분위 p 로. 세 자리
      ["BH q", r.bh_q == null ? 없음 : r.bh_q.toFixed(3)],
      ["관문 하한", f1(r.gate_lower)],
      ["앞 반", p(r.half_first_cagr)],
      ["뒤 반", p(r.half_second_cagr)],
      // 걷는 창(25.1947) — 겹치지 않는 12개월 창 가운데 초과 합 > 0 인 창 수 / 창 수. 3장 문턱("넷 가운데 셋")은 적용하지 않는다
      ["걷는 창", walkText(r)],
    ],
    sample: `${n(r.months)}개월 · 리밸런스 ${n(r.rebalances)} · 매수 ${n(r.buys)} · 묶음 대조 ${n(r.control_blocks)}/관문 ${n(r.gate_blocks)} · 판 뒤 매도 ${n(r.sells_scored)} · 누적 시도 ${n(r.trials_cumulative)}`,
    missing: [...까닭별].map(([why, cols]) => `${없음}: ${cols.join("·")} — ${why || "까닭 기록 없음"}`),
    stage: r.stage01_verdict == null ? null : `0→1(그림자로) 점검: ${r.stage01_verdict}${r.stage01_note ? ` — ${r.stage01_note}` : ""}`,
  };
}

/** 이 실행 기록이 판정판 자리를 채우는가 — 줄이 실렸거나(빈 배열 포함) 판정판이 깨진 까닭이 남았다 */
export function hasScoreboard(run: ScoreboardRun | undefined): run is ScoreboardRun {
  return run != null && (run.scoreboard != null || run.scoreboard_error != null);
}

/**
 * 어느 판정판을 보일까 (25.1941) — 고른 묶음을 낸 실행 기록(`SCOREBOARD_OF_GROUP`)에 판정판이 있으면(`scoreboard_error` 만
 * 있어도) 그것, 없으면 그 시장의 가장 최근 판정판(`LATEST_SCOREBOARD`, fallback), 그것도 없으면 null.
 * 고른 묶음 쪽이 차면 `최근` 은 보지 않는다 — 길은 `hasScoreboard(그묶음)` 이 거짓일 때만 그 질의를 돌린다
 */
export function pickScoreboard(
  그묶음: ScoreboardRun | undefined,
  최근: ScoreboardRun | undefined,
): { run: ScoreboardRun; fallback: boolean } | null {
  if (hasScoreboard(그묶음)) return { run: 그묶음, fallback: false };
  return 최근 ? { run: 최근, fallback: true } : null;
}

/** 실행 기록 머리 한 줄 — 어느 실행의 판정판인가.
 * 물러났을 때는 까닭을 단정하지 않는다 — 고른 묶음 기록에 판정판이 없는 까닭은 25.1940 전 실행만이 아니다(25.1941) */
export function scoreboardHead(run: Pick<ScoreboardRun, "job_name" | "started_at">, fallback: boolean): string {
  const 작업 = JOB_LABEL[run.job_name] ?? run.job_name;
  return fallback
    ? `고른 묶음의 실행 기록에서 판정판을 찾지 못해 가장 최근 판정판(${작업}, ${userTimeOf(run.started_at)} KST)을 보입니다 — 25.1940 전 실행·도는 중·실패·기록 깨짐 가운데 하나`
    : `${작업} 실행 (${userTimeOf(run.started_at)} KST)`;
}

/** 늘 비는 칸 — 배치 `scoreboard.WHY_*` 와 같은 뜻. 줄마다 되풀이하지 않고 한 번만 적는다 */
export const ALWAYS_EMPTY_NOTE =
  "수용량(보유 거래대금 미저장)·서로 다른 종목·끝난 거래는 아직 기록이 없어 싣지 않습니다. '0→1 점검' 은 docs/funnel.md 3장의 첫 값(확인 전)에 대어 본 것일 뿐 — 올림은 사람이 정합니다. 1→2 이후 문턱은 적용하지 않습니다.";

/**
 * 판정판이 싣지 않는 경고·비용 몫을 어디서 보나(25.1941) — **그 자리가 화면에 있을 때만** 가리킨다(25.1942). 경고 띠는
 * 경고가 있을 때만 그려지고, "종합 점수 (비용 0)" 줄은 월 실행 묶음에만 있다. 물러난 판정판(다른 실행)이면 위 표와
 * 견주지 말라고 적는다(경고·비용 0 줄은 가리키지 않는다 — 다른 실행이라). 물러났어도 결과 표(전략)가 없으면 null(25.1943).
 * 가리킬 곳이 없으면 null
 */
export function elsewhereNote(strategies: readonly string[], warningCount: number, fallback: boolean): string | null {
  // 물러났으면 위 결과와 다른 실행 — 결과 표가 없으면(묶음 없음) 가리킬 곳이 없다(25.1943)
  if (fallback) return strategies.length ? "이 판정판은 위 결과와 다른 실행의 것입니다 — 바로 견주지 않습니다." : null;
  const 글: string[] = [];
  if (warningCount > 0) 글.push("경고(생존편향·시점 유니버스 등)는 화면 맨 위 \"이 결과를 읽기 전에\" 에");
  if (strategies.includes("composite_no_cost")) 글.push("비용 몫은 위 결과 표의 \"종합 점수 (비용 0)\" 줄과 견주어 봅니다");
  return 글.length ? `${글.join(", ")}.` : null;
}

/**
 * 화면(`BacktestView`)이 `elsewhereNote` 에 넘기는 인자를 한 곳에서 낸다(25.1946, 25.1944 교차검증 [권고]) — 결과 표의
 * 전략·화면 맨 위 경고 띠와 같은 `mergeWarnings` 수·판정판이 물러났는지. 예전엔 화면이 셋을 직접 넘겨, 인자를 바꿔 끼우거나
 * 박아 넣어도 어떤 시험도 깨지지 않았다. 판정판이 없으면 null
 */
export function scoreboardNoteFor(
  data: { runs?: ReadonlyArray<{ strategy: string; warnings_json: string }>; scoreboard?: { fallback: boolean } | null } | null | undefined,
): string | null {
  if (!data?.scoreboard) return null;
  const runs = data.runs ?? [];
  return elsewhereNote(
    runs.map((r) => r.strategy),
    mergeWarnings([...runs]).length,
    data.scoreboard.fallback,
  );
}
