/**
 * 백테스트·스트레스 결과 조회와 실행 요청 (docs/backtest.md, docs/stress.md, Step 16).
 *
 * 웹은 **읽기와 깨우기만 한다**. 계산은 전부 batch/jobs/backtest.py · batch/jobs/stress.py 가 하고
 * 결과는 backtest_runs · backtest_curves · stress_runs 에 저장된다 (CLAUDE.md "웹앱은 계산하지 않는다").
 *
 * 화면 규칙 하나: **경고를 수익률보다 먼저 보여준다** (docs/backtest.md 4.3, docs/stress.md 3장).
 * 지금 데이터에는 생존편향이 항상 있다. 경고가 떨어진 숫자는 보이는 것보다 좋은 숫자다.
 */

import { z } from "zod";

// ----------------------------------------------------------------------
// 실행 요청 입력
// ----------------------------------------------------------------------

/** 워크플로 입력과 같은 집합이어야 한다 (.github/workflows/backtest.yml). 테스트로 고정한다. */
export const WHAT_OPTIONS = [
  "둘다",
  "백테스트",
  "스트레스",
  "장기문턱",
] as const;

export const runInputSchema = z.object({
  market: z.enum(["KR", "US"]),
  what: z.enum(WHAT_OPTIONS).default("둘다"),
  /** 백테스트 기간(년). 5년이 기본이고 저장된 가격이 짧으면 배치가 알아서 줄인다 */
  years: z.number().int().min(1).max(20).default(5),
  /** 매월 고르는 종목 수. docs/backtest.md 5장 "파라미터 최적화를 하지 않는다" 라 기본 20 에서 거의 안 바꾼다 */
  top_n: z.number().int().min(5).max(100).default(20),
  /** 시장 추세 오버레이 (docs/backtest.md 2.4). 켠 그룹과 끈 그룹을 나란히 보고 배수를 정한다 */
  trend_filter: z.boolean().default(false),
  /** 비교용. 시점 유니버스를 끄고 오늘 유니버스를 전 구간에 쓴다 (docs/backtest.md 1.3) */
  no_pit_universe: z.boolean().default(false),
});
export type RunInput = z.infer<typeof runInputSchema>;

// ----------------------------------------------------------------------
// 질의
// ----------------------------------------------------------------------

/**
 * 기본으로 보여 줄 실행 묶음. 한 번 돌리면 전략 여러 개가 같은 group_id 로 저장된다.
 *
 * **규칙 판단용 묶음을 먼저** (docs/infra.md 25.788, 백테스트 감사 #2). 예전에는 가장 최근 묶음이라, 비교용 실행(시점 유니버스 끔 —
 * 배치가 "규칙 판단에 쓰지 않는다" 고 적은 것)이나 추세 필터 켬, 장기 문턱만 돌린 묶음(종합 전략이 없다)을 한 번 돌리면 그것이
 * /backtest 의 대표 결과가 됐다 — 09-17 A/B 비교에서 끔 실행이 켬보다 뒤에 돌아 종합 28.4%(켬이면 23.5%)가 기본 화면에 섰다.
 * 종합 전략이 있고, 비교용 표시(`WARN_NO_HISTORICAL_UNIVERSE`·`WARN_TREND_ON` 문구)가 없는 묶음 중 최신 → 없으면 최신 묶음.
 * 표시는 배치 경고 글이라 문구를 바꾸면 여기도 바꾼다(`backtest.test.ts` 가 파이썬 문구와 같은지 본다)
 */
export const NO_PIT_MARK = "과거 유니버스 스냅샷 없음";
export const TREND_ON_MARK = "추세 필터 켬";
/**
 * 기간·상위 N 이 기본이 아닌 실행 (docs/infra.md 25.928). 배치 `WARN_NOT_DEFAULT_PARAMS` 와 같은 글이다.
 * 그 표시가 생기기 전 실행은 기간을 알 길이 없어 **상위 N 만** 본다(`top_n` 열) — 기본값은 배치 `bt.DEFAULT_TOP_N`
 */
export const NOT_DEFAULT_MARK = "기본 파라미터 아님";
export const DEFAULT_TOP_N = 20;
export const LATEST_GROUP = `SELECT group_id FROM backtest_runs WHERE market = ?
GROUP BY group_id
ORDER BY MAX(CASE WHEN strategy = 'composite' THEN 1 ELSE 0 END) DESC,
  MAX(CASE WHEN warnings_json LIKE '%${NO_PIT_MARK}%' OR warnings_json LIKE '%${TREND_ON_MARK}%'
    OR warnings_json LIKE '%${NOT_DEFAULT_MARK}%' OR top_n <> ${DEFAULT_TOP_N} THEN 1 ELSE 0 END) ASC,
  MAX(created_at) DESC, MAX(id) DESC
LIMIT 1`;

export const RUNS_IN_GROUP = `SELECT id, market, strategy, start_date, end_date, top_n, rebalance, costs_json,
  rebalances, final_equity, turnover_avg, excess_cagr, win_rate, metrics_json, warnings_json, group_id,
  calc_version, scoring_calc_version, created_at
FROM backtest_runs WHERE group_id = ? AND market = ? ORDER BY id`;

/** 지난 실행 묶음 목록(고르기용). 같은 묶음의 행이 여럿이라 묶어서 센다 */
/** **그 시장 것만** 센다 (docs/infra.md 25.581, 감사). 두 시장을 합쳐 12개를 가져와, 국내 탭 목록에 미국 묶음이 섞였고(고르면
 * 말없이 국내 최신으로 물러났다) 미국 실행이 12번 쌓이면 국내 과거 묶음이 목록에서 사라졌다 */
export const RECENT_GROUPS = `SELECT group_id, market, COUNT(*) AS strategies, MIN(start_date) AS start_date,
  MAX(end_date) AS end_date, MAX(top_n) AS top_n, MAX(created_at) AS created_at,
  MAX(CASE WHEN warnings_json LIKE '%${NO_PIT_MARK}%' THEN 1 ELSE 0 END) AS no_pit,
  MAX(CASE WHEN warnings_json LIKE '%${TREND_ON_MARK}%' THEN 1 ELSE 0 END) AS trend_on,
  MAX(CASE WHEN warnings_json LIKE '%${NOT_DEFAULT_MARK}%' OR top_n <> ${DEFAULT_TOP_N} THEN 1 ELSE 0 END) AS not_default,
  MAX(CASE WHEN strategy = 'composite' THEN 1 ELSE 0 END) AS has_composite
FROM backtest_runs WHERE market = ? GROUP BY group_id, market ORDER BY created_at DESC LIMIT 12`;

/** 묶음 목록에 붙이는 표시 — 어느 것이 비교용인지 (25.788) */
export function groupTags(g: {
  no_pit?: number | null;
  trend_on?: number | null;
  has_composite?: number | null;
  not_default?: number | null;
}): string {
  const 표: string[] = [];
  if (g.has_composite === 0) 표.push("장기 문턱만");
  if (g.no_pit) 표.push("비교용 · 시점 유니버스 끔");
  if (g.trend_on) 표.push("추세 필터 켬");
  if (g.not_default) 표.push("비교용 · 기본 파라미터 아님");
  return 표.length ? ` · ${표.join(" · ")}` : "";
}

/**
 * 지금 코드의 계산 판 — 배치 `services/backtest.CALC_VERSION`(엔진)·`services/scoring.CALC_VERSION`(점수)과 같아야 한다
 * (`backtest.test.ts` 가 파이썬 쪽을 읽어 대 본다). 결과의 판이 이보다 낮으면 **옛 규칙으로 낸 결과**라고 띠를 붙인다
 * (docs/infra.md 25.788, 백테스트 감사 #3 — 화면이 판 번호만 찍고 견주지 않아 09-17 결과가 지금 결과처럼 보였다)
 */
export const ENGINE_VERSION = 5; // 5: 표시통화가 다른 재무를 견주지 않는다 (25.919)
export const SCORING_VERSION = 10; // 9: 밸류 분모 시점 정합 (25.954) · 10: 옮기는 조건 고침 (25.960)

/** 엔진 판이 결과를 바꾼 시장 — 3판(25.783)은 국내 거래세만 바꿨다(25.789). 표에 없는 판(4: 마지막 날 평가만, 25.795)은 모든 시장 */
const 엔진_판_시장: Record<number, string[]> = { 3: ["KR"] };

function 엔진이_낡았나(calc: number, market: string | undefined): boolean {
  for (let v = calc + 1; v <= ENGINE_VERSION; v++) {
    const 시장 = 엔진_판_시장[v];
    if (!시장 || !market || 시장.includes(market)) return true;
  }
  return false;
}

export function staleVersionNote(run: { calc_version: number | null; scoring_calc_version: number | null; market?: string } | undefined): string | null {
  if (!run) return null;
  const 낡음: string[] = [];
  if (run.calc_version !== null && 엔진이_낡았나(run.calc_version, run.market)) 낡음.push(`엔진 ${run.calc_version} → 지금 ${ENGINE_VERSION}`);
  if (run.scoring_calc_version === null) 낡음.push(`점수 판 기록 없음 → 지금 ${SCORING_VERSION}`);
  else if (run.scoring_calc_version < SCORING_VERSION) 낡음.push(`점수 ${run.scoring_calc_version} → 지금 ${SCORING_VERSION}`);
  return 낡음.length
    ? `옛 계산 판으로 낸 결과입니다(${낡음.join(", ")}). 지금 규칙의 결과를 보려면 다시 돌려야 합니다 — 판이 다른 결과끼리는 수익률을 나란히 비교할 수 없습니다`
    : null;
}

/**
 * 자본곡선. 실행 하나가 5년이면 1,200행쯤이라 한 번에 서너 개만 가져온다.
 * 물음표 개수가 달라지므로 문장을 만들어 쓴다 (값은 항상 인자로 넘긴다. 문자열 이어붙이기 금지).
 */
export function curvesSql(runCount: number): string {
  const holes = Array(Math.max(runCount, 1)).fill("?").join(", ");
  return `SELECT run_id, date, equity, drawdown FROM backtest_curves WHERE run_id IN (${holes}) ORDER BY run_id, date`;
}

export const LATEST_STRESS = `SELECT id, market, as_of_date, weighting, cash_weight, basket_json, excluded_json,
  windows_json, skipped_json, curve_start, curve_end, warnings_json, calc_version, created_at
FROM stress_runs WHERE market = ? ORDER BY created_at DESC, id DESC LIMIT 1`;

/** 스트레스 계산 판 — 배치 `services/stress.CALC_VERSION` 과 같게 (2: 모든 종목에 가격이 있는 첫 날부터, 25.823) */
export const STRESS_CALC_VERSION = 3;

/** 옛 판 결과면 한 줄 — 스트레스는 손으로만 돌아 다시 돌리기 전까지 옛 결과가 그대로 보인다 (25.827, 교차검증) */
export function stressVersionNote(calcVersion: number | null | undefined): string | null {
  if (calcVersion === null || calcVersion === undefined || calcVersion >= STRESS_CALC_VERSION) return null;
  return "옛 계산 방식의 결과입니다 — 가격 이력이 짧은 종목의 비중이 현금(수익 0)으로 섞여 낙폭이 작게 나왔을 수 있습니다. 스트레스 테스트를 다시 돌리면 바뀝니다";
}

/** 그 시장의 마지막 스트레스 실행 — 신호 0건이라 돌지 않은 날을 알기 위해 (25.825) */
export const LATEST_STRESS_RUN = `SELECT status, started_at, step_log FROM batch_runs
WHERE job_name = 'stress' AND market = ? ORDER BY started_at DESC, id DESC LIMIT 1`;

/**
 * 마지막 스트레스 실행이 "그날 신호 없음" 으로 돌지 않았으면 한 줄 (docs/infra.md 25.825, 감사). 25.359 부터 그날은 결과 행을 쓰지 않아
 * 화면은 지난 바스켓의 낙폭을 기준일만 작게 단 채 보였다 — 지금 추천과 다른 바스켓이라는 말이 없었다
 */
export function stressNoSignalNote(
  run: { status: string; step_log: string | null } | undefined, stressAsOf: string | null,
): string | null {
  if (!run || run.status !== "success" || !run.step_log) return null;
  let log: { note?: unknown; as_of?: unknown } | null = null;
  try {
    log = JSON.parse(run.step_log) as { note?: unknown; as_of?: unknown };
  } catch {
    return null;
  }
  if (log?.note !== "그날 신호 없음" || typeof log.as_of !== "string") return null;
  if (stressAsOf && log.as_of <= stressAsOf) return null;
  const 아래 = stressAsOf ? `아래는 ${stressAsOf} 바스켓입니다 — 지금 추천과 다릅니다` : "앞서 계산한 결과도 없습니다";
  return `${log.as_of} 에는 걸린 신호가 없어 스트레스 시험을 돌리지 않았습니다. ${아래}`;
}

/** 바스켓에 든 종목 이름. 숫자 id 만 보여 주면 무엇을 봤는지 확인할 수 없다 */
export function stocksSql(count: number): string {
  const holes = Array(Math.max(count, 1)).fill("?").join(", ");
  return `SELECT id, ticker, COALESCE(name_ko, name_en, ticker) AS name FROM stocks WHERE id IN (${holes})`;
}

/** 실행 상태. 화면은 이것만 폴링한다 (결과는 끝난 뒤에 읽는다) */
export const RECENT_BATCH_RUNS = `SELECT id, job_name, market, status, started_at, finished_at, step_log, error_text
FROM batch_runs WHERE job_name IN ('backtest', 'stress') ORDER BY started_at DESC, id DESC LIMIT 6`;

// ----------------------------------------------------------------------
// 표시
// ----------------------------------------------------------------------

const STRATEGY_LABEL: Record<string, string> = {
  composite: "종합 점수",
  composite_no_cost: "종합 점수 (비용 0)",
  value: "밸류만",
  quality: "퀄리티만",
  growth: "성장만",
  momentum: "모멘텀만",
  risk: "리스크만",
  benchmark: "벤치마크 (유니버스 동일가중)",
  // 발굴 루프 후보 — 실제 추천에는 아직 안 쓴다 (docs/factors.md 12장, docs/infra.md 25.439)
  momentum_sector: "모멘텀 + 업종 상위 절반 (후보)",
};

/** long_q55 처럼 문턱이 붙는 전략은 규칙으로 읽는다 (docs/backtest.md 7장) */
export function strategyLabel(strategy: string): string {
  const known = STRATEGY_LABEL[strategy];
  if (known) return known;
  const long = /^long_q(\d+)$/.exec(strategy);
  if (long) return `장기 신호 문턱 ${long[1]}`;
  return strategy;
}

/** 표에서 묶어 보여 주는 갈래. 팩터 하나짜리는 성과요인분석이다 (docs/backtest.md 2.2) */
/** 발굴 루프로 넣은 **후보** 전략 — batch/jobs/backtest.CANDIDATE_STRATEGIES 와 같아야 한다 (docs/infra.md 25.443) */
export const CANDIDATE_STRATEGIES = ["momentum_sector"] as const;

export function strategyKind(strategy: string): "main" | "factor" | "long" | "candidate" {
  if (strategy === "benchmark" || strategy.startsWith("composite"))
    return "main";
  if (/^long_q\d+$/.test(strategy)) return "long";
  // 후보는 성과요인분석이 아니다 — 예전에는 "성과요인" 표가 붙어 팩터 묶음에 섞였다 (25.443, 교차검증)
  if ((CANDIDATE_STRATEGIES as readonly string[]).includes(strategy)) return "candidate";
  return "factor";
}

/**
 * 비용 표시. 열쇠 이름은 batch/services/backtest.Costs 의 필드와 같아야 한다.
 * (한 번 어긋나서 화면에 전부 "-" 가 찍혔다. 그래서 테스트가 파이썬 쪽 필드 이름을 함께 본다.)
 *
 * 기본값에는 `[확인필요]` 가 붙어 있다(docs/backtest.md 3장). 숫자만 보여 주고 근거를 숨기지 않는다.
 */
export const COST_KEYS = ["commission_pct", "tax_sell_pct", "slippage_pct"] as const;

export function costLine(costsJson: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(costsJson);
  } catch {
    return "비용 정보를 읽지 못했습니다";
  }
  const get = (key: string) => {
    const v = (parsed as Record<string, unknown>)?.[key];
    return typeof v === "number" ? `${v}%` : "-";
  };
  return `편도 수수료 ${get("commission_pct")} · 매도 거래세 ${taxText(parsed, get("tax_sell_pct"))} · 편도 슬리피지 ${get("slippage_pct")}`;
}

/**
 * 국내 거래세는 **체결일의 법정 세율**이다 (docs/infra.md 25.783). 배치가 `costs_json.tax_schedule` 에 [시행일, %] 목록을 적는다.
 * 그 전 실행(목록 없음)은 한 값이 전 구간을 덮었다 — 그대로 한 값만 적는다
 */
function taxText(parsed: unknown, flat: string): string {
  const sched = (parsed as Record<string, unknown>)?.tax_schedule;
  if (!Array.isArray(sched) || sched.length === 0) return flat;
  const 쌍 = sched.filter(
    (e): e is [string, number] => Array.isArray(e) && typeof e[0] === "string" && typeof e[1] === "number",
  );
  if (쌍.length === 0) return flat;
  const [첫, ...뒤] = 쌍;
  const 목록 = 뒤.map(([d, pct]) => `${d.slice(0, 7)}~ ${pct}%`).join(" · ");
  return `체결일 세율(${목록}${뒤.length ? ", " : ""}그 전 ${첫[1]}%)`;
}

const COST_LABEL: Record<string, string> = { commission: "수수료", tax: "거래세", slippage: "슬리피지" };

/**
 * 어느 비용이 **기본값**(아직 실측 아님)인지 (docs/infra.md 25.358). 배치가 `costs_json.defaults` 에 적는다.
 * 예전에는 화면이 늘 "(기본값은 아직 실측 아님)" 을 붙여 설정 값을 쓴 실행도 기본값처럼 읽혔다.
 * 25.358 전 실행에는 기록이 없다 — 그때는 모른다고 적는다.
 */
export function costDefaultsNote(costsJson: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(costsJson);
  } catch {
    return "";
  }
  const d = (parsed as Record<string, unknown>)?.defaults;
  if (!Array.isArray(d)) return "(설정값·기본값 구분 기록 없음)";
  // 거래세가 **시점별 법정 표**면(25.783) 지난 연도는 설정도 "아직 실측 아닌 기본값" 도 아니다 — 따로 말한다 (25.787, 교차검증)
  const sched = (parsed as Record<string, unknown>)?.tax_schedule;
  const 법정 = Array.isArray(sched) && sched.length > 1;
  const 남은 = d.filter((k) => !(법정 && String(k) === "tax"));
  const 뒤 = 법정 ? (d.map(String).includes("tax") ? " · 거래세는 법정 세율 표" : " · 지난 연도 거래세는 법정 세율 표, 올해분만 설정값") : "";
  if (남은.length === 0) return `(모두 설정값${뒤})`;
  return `(기본값·아직 실측 아님: ${남은.map((k) => COST_LABEL[String(k)] ?? String(k)).join(", ")}${뒤})`;
}

export interface BacktestRun {
  id: number;
  market: string;
  strategy: string;
  start_date: string;
  end_date: string;
  top_n: number;
  rebalance: string;
  costs_json: string;
  rebalances: number;
  final_equity: number;
  turnover_avg: number | null;
  excess_cagr: number | null;
  win_rate: number | null;
  metrics_json: string;
  warnings_json: string;
  group_id: string;
  /** 엔진 판 (`services/backtest.CALC_VERSION`). 리밸런스·비용·곡선을 내는 방식 */
  calc_version: number;
  /**
   * **점수 계산식의 판** (`services/scoring.CALC_VERSION`). 0041 이전 실행은 null.
   *
   * 백테스트 결과를 실제로 가르는 것은 이쪽이다 — 어떤 종목을 고르느냐가 곧 수익률이다.
   * 2026-09-22 까지 이 값이 어디에도 안 남아, 판이 다른 실행들이 표에서 똑같아 보였다
   * (docs/infra.md 25.101).
   */
  scoring_calc_version: number | null;
  created_at: string;
}

export interface CurvePoint {
  run_id: number;
  date: string;
  equity: number;
  /** 고점 대비 낙폭(≤0). 배치가 저장할 때 낸다(docs/backtest.md 8.4). 0032 이전 실행은 null */
  drawdown: number | null;
}

/** 실행 중인 배치의 진행 글. step_log 의 progress 가 없으면 null */
export function progressText(row: Pick<BatchRunRow, "status" | "step_log"> | undefined): string | null {
  if (!row || row.status !== "running" || !row.step_log) return null;
  try {
    const p = (JSON.parse(row.step_log) as { progress?: { done?: number; total?: number; current?: string } }).progress;
    if (!p || typeof p.done !== "number" || typeof p.total !== "number") return null;
    return `${p.done}/${p.total}${p.current ? ` · ${p.current}` : ""}`;
  } catch {
    return null;
  }
}

/**
 * 어느 실행의 자본곡선을 그릴지 고른다.
 *
 * 기본값(종합·벤치마크)이 그 묶음에 없을 수 있다. 운영 DB 의 최신 묶음이 실제로 그렇다 —
 * 장기 문턱 비교(`--only-long`)는 benchmark 와 long_q* 만 저장한다. 그때 빈 차트를 보여 주는
 * 대신 그 묶음의 앞쪽 실행을 그린다. 화면이 비면 결과가 아예 없는 줄 안다.
 */
export function pickCurveRuns<T extends { id: number; strategy: string }>(
  runs: T[],
  wanted: string[],
  max: number,
): T[] {
  const matched = runs.filter((r) => wanted.includes(r.strategy));
  return (matched.length > 0 ? matched : runs).slice(0, max);
}

export interface StressRun {
  id: number;
  market: string;
  as_of_date: string;
  weighting: string;
  cash_weight: number;
  basket_json: string;
  excluded_json: string;
  windows_json: string;
  skipped_json: string;
  curve_start: string;
  curve_end: string;
  warnings_json: string;
  calc_version: number;
  created_at: string;
}

export interface StressWindow {
  length: number;
  start: string;
  end: string;
  return_pct: number;
  max_drawdown: number;
  recovery_days: number | null;
}

export interface BatchRunRow {
  id: number;
  job_name: string;
  market: string | null;
  status: string;
  started_at: string;
  finished_at: string | null;
  step_log: string | null;
  error_text: string | null;
}

/**
 * 이보다 오래 `running` 인 기록은 **멈춘 것**으로 본다 (docs/infra.md 25.581, 감사). 워크플로 `timeout-minutes: 60` 에 여유를 둔 값.
 * 러너가 취소·강제 종료되면 기록이 `running` 으로 남는데(파이썬 `except Exception` 이 못 잡는 종료), 정리는 6시간 뒤 같은 작업이
 * 다시 시작될 때만 한다. 그동안 두 시장 모두 "실행 중" 버튼이 잠기고 20초 폴링이 끝없이 돌아 화면에서 풀 길이 없었다
 */
export const BACKTEST_RUNNING_MAX_MINUTES = 75;

function 멈춤(r: BatchRunRow, now: Date): boolean {
  const 시작 = Date.parse(r.started_at);
  return Number.isFinite(시작) && now.getTime() - 시작 > BACKTEST_RUNNING_MAX_MINUTES * 60_000;
}

/** 배치가 아직 도는 중인가. 화면의 "실행 중" 띠와 폴링 여부를 정한다. 오래 멈춘 기록은 세지 않는다 (25.581) */
export function isRunning(rows: BatchRunRow[], now: Date = new Date()): boolean {
  return rows.some((r) => r.status === "running" && !멈춤(r, now));
}

/** 멈춘 것으로 보이는 실행이 있으면 그 사실을 한 줄로 (25.581). 새로 실행을 요청해도 된다고 알린다 */
export function stuckRunNote(rows: BatchRunRow[], now: Date = new Date()): string | null {
  const 멈춘 = rows.filter((r) => r.status === "running" && 멈춤(r, now));
  if (!멈춘.length) return null;
  return `${멈춘.map((r) => `${r.job_name}${r.market ? `(${r.market})` : ""}`).join(", ")} 기록이 ${BACKTEST_RUNNING_MAX_MINUTES}분 넘게`
    + " '실행 중' 입니다 — 취소되거나 멈춘 것으로 보고 새 실행을 막지 않습니다. Actions 기록에서 확인하세요";
}

/** 지표 JSON 에서 숫자만 꺼낸다. 없는 값은 null 로 두고 화면은 "-" 를 찍는다 */
export function metricNumber(raw: unknown, key: string): number | null {
  if (typeof raw !== "object" || raw === null) return null;
  const value = (raw as Record<string, unknown>)[key];
  return typeof value === "number" ? value : null;
}

/** 여러 전략의 경고를 합친다. 같은 문장이 전략 수만큼 반복되면 읽히지 않는다 */
export function mergeWarnings(
  runs: Array<{ warnings_json: string }>,
): string[] {
  const seen = new Set<string>();
  let 못읽음 = 0;
  for (const run of runs) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(run.warnings_json);
    } catch {
      못읽음 += 1;
      continue;
    }
    if (Array.isArray(parsed)) {
      for (const w of parsed) if (typeof w === "string") seen.add(w);
    } else {
      못읽음 += 1;
    }
  }
  // **못 읽은 경고를 "경고 없음" 으로 만들지 않는다** (docs/infra.md 25.581, 감사). 생존편향 같은 반드시 붙는 경고가
  // 빠지면 결과가 깨끗해 보인다
  const out = [...seen].sort();
  if (못읽음) out.unshift(`전략 ${못읽음}개의 경고를 읽지 못했습니다 — 빠진 경고가 있을 수 있습니다`);
  return out;
}

/**
 * 스트레스 결과의 JSON 칸들을 모양까지 확인해 읽는다 (docs/infra.md 25.581, 감사). 예전에는 `basket_json` 이 깨지면
 * `Object.keys(null)` 이 TypeError 를 내 **백테스트 화면 전체가** 그려지지 않았고, `windows_json` 이 깨지면 안내 없이 빈 표였다.
 * 못 읽은 칸은 빈 값으로 두고 이름을 `unreadable` 에 모은다 — 화면이 그 사실을 적는다
 */
export function readStressJson(stress: Pick<StressRun, "windows_json" | "warnings_json" | "basket_json" | "excluded_json" | "skipped_json">) {
  const unreadable: string[] = [];
  const 읽기 = <T,>(raw: string | null | undefined, 이름: string, 맞나: (v: unknown) => boolean, 빈: T): T => {
    if (raw === null || raw === undefined) return 빈;
    try {
      const v: unknown = JSON.parse(raw);
      if (맞나(v)) return v as T;
    } catch {
      // 아래에서 못 읽었다고 적는다
    }
    unreadable.push(이름);
    return 빈;
  };
  const 객체 = (v: unknown) => typeof v === "object" && v !== null && !Array.isArray(v);
  return {
    windows: 읽기<StressWindow[]>(stress.windows_json, "구간", Array.isArray, []),
    warnings: 읽기<string[]>(stress.warnings_json, "경고", Array.isArray, []),
    basket: 읽기<Record<string, number>>(stress.basket_json, "바스켓", 객체, {}),
    excluded: 읽기<number[]>(stress.excluded_json, "제외 종목", Array.isArray, []),
    skipped: 읽기<number[]>(stress.skipped_json, "건너뛴 구간", Array.isArray, []),
    unreadable,
  };
}

// ----------------------------------------------------------------------
// 실행 요청 (GitHub Actions 깨우기)
// ----------------------------------------------------------------------

export interface DispatchResult {
  dispatched: boolean;
  /** 어느 길로 깨웠는지. 토큰 권한에 따라 달라져서 화면과 로그에 남긴다 */
  via?: "workflow_dispatch" | "repository_dispatch";
  reason?: string;
}

export const WORKFLOW_FILE = "backtest.yml";
/** workflow_dispatch 는 브랜치를 지정해야 한다. 운영은 main 하나뿐이다 */
export const WORKFLOW_REF = "main";

/**
 * 백테스트 워크플로를 깨운다.
 *
 * 두 길을 다 쓴다. 이유:
 *   - `workflow_dispatch` 는 파라미터(시장·기간·N)를 그대로 넘길 수 있어 설계서가 고른 방식이다.
 *     대신 토큰에 **Actions: write** 권한이 필요하다
 *   - 포트폴리오 재계산이 이미 쓰는 `repository_dispatch` 는 **Contents: write** 면 된다.
 *     파라미터는 client_payload 로 넘기고 워크플로가 읽는다
 * 토큰에 어떤 권한이 들어 있는지 저장소 코드로는 알 수 없어 [확인필요], 먼저 workflow_dispatch 를
 * 부르고 권한 문제(403·404)면 repository_dispatch 로 한 번 더 시도한다. 둘 다 안 되면 이유를 돌려준다.
 */
export async function requestBacktest(
  rawInput: z.input<typeof runInputSchema>,
  fetchImpl: typeof fetch = fetch,
): Promise<DispatchResult> {
  const input: RunInput = runInputSchema.parse(rawInput);
  const token = process.env.GH_DISPATCH_TOKEN;
  const repo = process.env.GH_REPO;
  if (!token || !repo) {
    return {
      dispatched: false,
      reason:
        "GH_DISPATCH_TOKEN·GH_REPO 가 없어 Actions 를 깨울 수 없습니다. GitHub 에서 직접 실행하세요",
    };
  }
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "Content-Type": "application/json",
  };
  // 워크플로 입력은 전부 문자열이다. 숫자를 그대로 넣으면 GitHub 가 422 로 돌려보낸다
  // 켠 것만 넣는다. 워크플로가 `client_payload.x && '--flag'` 로 읽는데 문자열 "false" 도 참이라
  // 끈 값을 보내면 플래그가 켜진다. 없는 키는 거짓이다
  const inputs: Record<string, string> = {
    what: input.what,
    market: input.market,
    years: String(input.years),
    top_n: String(input.top_n),
    ...(input.trend_filter ? { trend_filter: "true" } : {}),
    ...(input.no_pit_universe ? { no_pit_universe: "true" } : {}),
  };

  try {
    const byWorkflow = await fetchImpl(
      `https://api.github.com/repos/${repo}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({ ref: WORKFLOW_REF, inputs }),
        cache: "no-store",
      },
    );
    if (byWorkflow.status === 204)
      return { dispatched: true, via: "workflow_dispatch" };
    if (byWorkflow.status !== 403 && byWorkflow.status !== 404) {
      return {
        dispatched: false,
        reason: `GitHub 가 ${byWorkflow.status} 를 돌려줬습니다`,
      };
    }

    // 권한이 모자란 경우에만 두 번째 길. 워크플로가 client_payload 를 읽는다
    const byRepo = await fetchImpl(
      `https://api.github.com/repos/${repo}/dispatches`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({
          event_type: "backtest",
          client_payload: inputs,
        }),
        cache: "no-store",
      },
    );
    if (byRepo.status === 204)
      return { dispatched: true, via: "repository_dispatch" };
    return {
      dispatched: false,
      reason: `GitHub 가 ${byWorkflow.status}·${byRepo.status} 를 돌려줬습니다. 토큰 권한(Actions 또는 Contents 쓰기)을 확인하세요`,
    };
  } catch (error) {
    return {
      dispatched: false,
      reason: error instanceof Error ? error.message : "호출에 실패했습니다",
    };
  }
}

/**
 * 배당을 넣었는지 — **나라마다 다르다** (docs/infra.md 25.244). 미국 시세는 야후 수정종가(배당 재투자 포함 총수익),
 * 국내 수정주가는 분할만 조정한다. 예전에는 두 나라 모두 "배당 재투자는 넣지 않았습니다" 라고 적었다.
 */
export function dividendNote(country: string): string {
  return country === "US"
    ? "미국은 야후 수정종가라 배당 재투자가 들어 있습니다(총수익) — 국내 결과와 바로 견주지 마세요."
    : "배당 재투자는 넣지 않았습니다(가격 수익).";
}

/**
 * 화면 위에 붙일 안내 (25.581). 고른 묶음이 그 시장에 없어 최신으로 물러났으면 그렇다고, 지난 묶음을 보는데 스트레스는
 * 늘 최신 실행 것이면 그렇다고 말한다 — docs/backtest.md 8장 "같은 배치에서 나오고 같이 읽어야" 와 어긋나는 것을 숨기지 않는다
 */
export function groupNotice(
  asked: string | null, shown: string | null, latest: string | null, hasStress: boolean, newest: string | null = latest,
): string | null {
  const 말: string[] = [];
  // 25.788 부터 물러나는 곳은 "가장 최근" 이 아니라 **기본 묶음(규칙 판단용 최신)** 이다 (25.790, 교차검증)
  if (asked && shown !== asked) 말.push("고른 묶음이 이 시장에 없어 기본 묶음(규칙 판단용 최신)을 보여 줍니다");
  // 스트레스는 묶음과 상관없이 **가장 최근 실행** 것이라 가장 최근 묶음(`newest`)과 견준다 — 기본 묶음과 견주면 비교용 실행 뒤 기본 화면에서
  // 안내가 사라지고, 그 최신 비교용 묶음을 고르면 같은 실행의 스트레스인데도 "이 묶음이 아니다" 라고 했다 (25.790)
  if (hasStress && shown && newest && shown !== newest) 말.push("아래 스트레스 결과는 이 묶음이 아니라 가장 최근 실행의 것입니다");
  return 말.length ? 말.join(". ") : null;
}

/**
 * 요청 뒤 러너가 실행 기록을 열기 전까지(체크아웃·설치 1~2분) 버튼을 잠가 둘 시간 (docs/infra.md 25.585).
 * 그 사이 누른 요청은 GitHub 대기열에서 앞 대기 요청을 밀어낸다 — 웹은 매번 "요청했습니다" 라고 했다
 */
export const REQUEST_GRACE_MINUTES = 5;

/**
 * 방금 요청했는데 아직 그 실행 기록이 안 보이는가 (25.585). 보이거나 5분이 지나면 풀린다.
 * **요청 뒤에도 살아 있는 기록만** 이번 것으로 친다 (25.589, 교차검증) — 시각만 보면 요청 50초 전에 시작해 이미 끝난 앞 실행의
 * 스트레스 행이 1분 여유 안에 들어 곧바로 풀렸다. 도는 중이거나, 요청 뒤에 끝난 것만 센다.
 * 두 시각은 다른 시계다(러너 UTC vs 브라우저) — 브라우저가 몇 분 느리면 여전히 틀릴 수 있다 [알고 둔다]
 */
export function awaitingRunner(requestedAt: number | null, rows: BatchRunRow[], now: number = Date.now()): boolean {
  if (requestedAt === null || now - requestedAt > REQUEST_GRACE_MINUTES * 60_000) return false;
  return !rows.some(
    (r) =>
      Date.parse(r.started_at) >= requestedAt - 60_000
      && (r.status === "running" || (r.finished_at !== null && Date.parse(r.finished_at) >= requestedAt)),
  );
}

/**
 * 폴링을 이어 갈까 (25.589, 교차검증). "둘다" 실행은 백테스트 행이 끝나고 스트레스 행이 열리기까지 몇 초 틈이 있어, 그 틈에 읽으면
 * `isRunning` 이 거짓이 되어 폴링이 멈추고 스트레스 단계 내내 버튼이 열려 있었다. 마지막 기록이 2분 안에 끝났으면 계속 읽는다
 */
export function recentlyFinished(rows: BatchRunRow[], now: number = Date.now()): boolean {
  // **다음 단계가 올 수 있는 경우만** — 성공한 백테스트 뒤에 스트레스가 열린다. 실패로 끝났으면 다음 단계가 없어,
  // 잠그면 바로 다시 돌리려는 사람을 2분 막고 "실행 중" 이라 틀린 말을 했다 (25.594, 교차검증)
  return rows.some(
    (r) => r.job_name === "backtest" && r.status === "success" && r.finished_at !== null
      && now - Date.parse(r.finished_at) < 2 * 60_000,
  );
}


/**
 * 결과 행의 노출 — 투자한 달 비율·평균 보유·첫 투자일 (docs/infra.md 25.786, 백테스트 감사 #1). 배치가 `metrics_json` 에 싣는다.
 * 그 전 실행에는 없다 — "-" 로 둔다
 */
export function exposureText(m: unknown): { invested: string; holdings: string; first: string | null } {
  const o = (m && typeof m === "object" ? m : {}) as Record<string, unknown>;
  const 투자 = typeof o.invested_share === "number" && Number.isFinite(o.invested_share) ? o.invested_share : null;
  const 보유 = typeof o.avg_holdings === "number" && Number.isFinite(o.avg_holdings) ? o.avg_holdings : null;
  // **내려 적는다** — 배치 경고와 같은 규칙. 2.96 이 "3.0종목" 으로 보이면 "3종목 아래" 경고와 모순된다 (25.790, 교차검증).
  // 부동소수 오차(0.57×100 = 56.99…)로 한 단위 더 내려가지 않게 먼저 반올림한다
  const 내림 = (x: number, 자리: number) => Math.floor(Number((x * 10 ** 자리).toFixed(6))) / 10 ** 자리;
  return {
    invested: 투자 === null ? "-" : `${내림(투자 * 100, 0)}%`,
    holdings: 보유 === null ? "-" : `${내림(보유, 2).toFixed(2)}종목`,
    first: typeof o.first_invested === "string" ? o.first_invested : null,
  };
}


/**
 * 우연일 확률 (docs/backtest.md 9장, 25.997) — 배치가 `metrics_json.luck` 에 싣는다(1 − DSR). 그 전 실행·벤치마크에는 없다.
 * 칸은 짧게, 펼친 설명(title)에 근거를 적는다.
 */
export function luckText(m: unknown): { cell: string; title: string | null } {
  const o = (m && typeof m === "object" ? m : {}) as Record<string, unknown>;
  const l = (o.luck && typeof o.luck === "object" ? o.luck : null) as Record<string, unknown> | null;
  if (!l) return { cell: "-", title: null };
  if (typeof l.luck_pct !== "number") return { cell: "판정 안 함", title: typeof l.verdict === "string" ? l.verdict : null };
  const num = (k: string) => (typeof l[k] === "number" ? (l[k] as number) : NaN);
  return {
    cell: `${l.luck_pct.toFixed(0)}%`,
    title: `DSR — 같은 실행에서 시험한 전략 ${num("n_trials")}개, ${num("months")}개월. 월 초과 샤프 ${num("sr_monthly").toFixed(2)} vs 우연 문턱 ${num("sr_star").toFixed(2)}. PSR(문턱 0) ${(num("psr") * 100).toFixed(0)}%`,
  };
}
