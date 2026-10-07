/**
 * 백테스트 화면의 조회·실행 요청 테스트 (docs/backtest.md 8장).
 *
 * 질의는 실제 마이그레이션을 적용한 SQLite 에 돌린다. 계산은 batch 쪽 테스트가 검증하고,
 * 여기서는 **저장된 값을 그대로 읽어 오는지**와 **워크플로를 제대로 깨우는지**만 본다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
  COST_KEYS, DEFAULT_TOP_N, ENGINE_VERSION, NOT_DEFAULT_MARK, NO_PIT_MARK, SCORING_VERSION, TREND_ON_MARK, groupTags, staleVersionNote, LATEST_GROUP, LATEST_STRESS, RECENT_BATCH_RUNS, RECENT_GROUPS, RUNS_IN_GROUP, WHAT_OPTIONS,
  WORKFLOW_FILE, WORKFLOW_REF, awaitingRunner, recentlyFinished, costLine, curvesSql, groupNotice, isRunning, readStressJson, stuckRunNote, mergeWarnings, metricNumber, progressText, requestBacktest,
  pickCurveRuns, runInputSchema, stocksSql, strategyKind, strategyLabel,
  type BacktestRun, type BatchRunRow,
} from "@/lib/backtest";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

beforeAll(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  const metrics = JSON.stringify({ cagr: 0.12, mdd: -0.31, sharpe: 0.8, sortino: 1.1, volatility_ann: 0.22 });
  const costs = JSON.stringify({ commission_pct: 0.015, tax_sell_pct: 0.18, slippage_pct: 0.1 });
  const warn = JSON.stringify(["생존편향: 상장폐지 종목이 DB 에 없습니다", "표본 부족: 리밸런스 12회 미만"]);
  const insert = db.prepare(
    `INSERT INTO backtest_runs (id, market, strategy, start_date, end_date, top_n, rebalance, costs_json, rebalances,
      final_equity, turnover_avg, excess_cagr, win_rate, metrics_json, warnings_json, group_id, calc_version, created_at)
     VALUES (?, ?, ?, '2021-09-01', '2026-09-01', 20, 'monthly', ?, 26, ?, 0.4, ?, ?, ?, ?, ?, 1, ?)`,
  );
  // 최신 묶음 g2(국내) 와 지난 묶음 g1(국내), 미국 묶음 g3
  insert.run(1, "KR", "composite", costs, 1.61, 0.03, 0.58, metrics, warn, "g1", "2026-09-16T00:00:00Z");
  insert.run(2, "KR", "benchmark", costs, 1.4, null, null, metrics, warn, "g1", "2026-09-16T00:00:00Z");
  insert.run(3, "KR", "composite", costs, 1.75, 0.05, 0.6, metrics, warn, "g2", "2026-09-17T00:00:00Z");
  insert.run(4, "KR", "benchmark", costs, 1.42, null, null, metrics, warn, "g2", "2026-09-17T00:00:00Z");
  insert.run(5, "KR", "value", costs, 1.2, -0.02, 0.44, metrics, warn, "g2", "2026-09-17T00:00:00Z");
  insert.run(6, "US", "composite", costs, 1.3, 0.01, 0.5, metrics, warn, "g3", "2026-09-15T00:00:00Z");

  const curve = db.prepare("INSERT INTO backtest_curves (run_id, date, equity) VALUES (?, ?, ?)");
  curve.run(3, "2026-09-01", 1.0);
  curve.run(3, "2026-09-02", 1.02);
  curve.run(4, "2026-09-01", 1.0);
  curve.run(4, "2026-09-02", 0.99);

  db.exec(`
    INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
    VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't');
    INSERT INTO stress_runs (market, as_of_date, weighting, cash_weight, basket_json, excluded_json, windows_json,
      skipped_json, curve_start, curve_end, warnings_json, calc_version, created_at)
    VALUES ('KR', '2026-09-16', 'suggested', 0.3, '{"1": 0.7}', '[]',
      '[{"length": 20, "start": "2025-04-01", "end": "2025-04-30", "return_pct": -0.18, "max_drawdown": -0.2, "recovery_days": null}]',
      '[250]', '2021-09-01', '2026-09-16', '["생존편향: 살아남은 종목만 들어 있습니다"]', 1, '2026-09-17T00:00:00Z');
    INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status)
    VALUES ('backtest', 'KR', '2026-09-17', 'manual', '2026-09-17T00:00:00Z', 'running');
    INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, finished_at, status)
    VALUES ('stress', 'KR', '2026-09-16', 'manual', '2026-09-16T00:00:00Z', '2026-09-16T00:05:00Z', 'success');
  `);
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("질의", () => {
  it("가장 최근 묶음을 시장별로 고른다", () => {
    expect((db.prepare(LATEST_GROUP).get("KR") as { group_id: string }).group_id).toBe("g2");
    expect((db.prepare(LATEST_GROUP).get("US") as { group_id: string }).group_id).toBe("g3");
  });

  it("묶음 안의 전략을 모두 준다", () => {
    const rows = db.prepare(RUNS_IN_GROUP).all("g2", "KR") as unknown as BacktestRun[];
    // 묶음은 그 시장 것만 (docs/infra.md 25.313) — 같은 묶음을 미국으로 물으면 비어야 한다
    expect(db.prepare(RUNS_IN_GROUP).all("g2", "US")).toEqual([]);
    expect(rows.map((r) => r.strategy)).toEqual(["composite", "benchmark", "value"]);
    expect(rows[0].final_equity).toBe(1.75);
  });

  it("지난 묶음 목록은 그 시장 것만, 최근 것이 먼저 (25.581)", () => {
    const rows = db.prepare(RECENT_GROUPS).all("KR") as Array<{ group_id: string; strategies: number }>;
    expect(rows.map((r) => r.group_id), "미국 묶음(g3)이 국내 목록에 섞이면 안 된다").toEqual(["g2", "g1"]);
    expect(rows[0].strategies).toBe(3);
    expect((db.prepare(RECENT_GROUPS).all("US") as Array<{ group_id: string }>).map((r) => r.group_id)).toEqual(["g3"]);
  });

  it("물러났거나 스트레스가 다른 실행이면 말한다 (25.581)", () => {
    expect(groupNotice(null, "g2", "g2", true)).toBeNull();
    expect(groupNotice("g3", "g2", "g2", true)).toContain("이 시장에 없어");
    expect(groupNotice("g1", "g1", "g2", true)).toContain("스트레스 결과는 이 묶음이 아니라");
    expect(groupNotice("g1", "g1", "g2", false)).toBeNull();
    // 스트레스는 가장 최근 묶음(newest)과 견준다 — 기본 묶음과 다를 수 있다 (25.790)
    expect(groupNotice(null, "본", "본", true, "끔")).toContain("스트레스 결과는 이 묶음이 아니라");
    expect(groupNotice("끔", "끔", "본", true, "끔")).toBeNull();
    expect(groupNotice("g3", "본", "본", false, "끔")).toContain("기본 묶음(규칙 판단용 최신)");
  });

  it("못 읽은 경고·스트레스 칸을 없는 것으로 만들지 않는다 (25.581)", () => {
    expect(mergeWarnings([{ warnings_json: "x" }, { warnings_json: '["생존편향"]' }])[0]).toContain("경고를 읽지 못했습니다");
    const 읽음 = readStressJson({ windows_json: "x", warnings_json: "[]", basket_json: "null", excluded_json: "[]", skipped_json: "[]" });
    expect(읽음.basket, "Object.keys(null) 로 화면이 죽지 않게").toEqual({});
    expect(읽음.unreadable).toEqual(["구간", "바스켓"]);
  });

  it("자본곡선은 고른 실행만, 물음표 개수가 인자와 맞는다", () => {
    const sql = curvesSql(2);
    expect(sql.match(/\?/g)).toHaveLength(2);
    const rows = db.prepare(sql).all(3, 4) as Array<{ run_id: number; equity: number; drawdown: number | null }>;
    expect(rows).toHaveLength(4);
    expect(rows[0].run_id).toBe(3);
    // 낙폭 열이 있고, 0032 이전 행은 null 이다 (docs/backtest.md 8.4)
    expect(rows[0].drawdown).toBeNull();
  });

  it("실행 중 진행 글은 step_log 의 progress 에서", () => {
    expect(progressText({ status: "running", step_log: JSON.stringify({ progress: { done: 3, total: 8, current: "momentum" } }) })).toBe("3/8 · momentum");
    expect(progressText({ status: "success", step_log: JSON.stringify({ progress: { done: 8, total: 8 } }) })).toBeNull();
    expect(progressText({ status: "running", step_log: "{" })).toBeNull();
    expect(progressText(undefined)).toBeNull();
  });

  it("기본 전략이 그 묶음에 없으면 앞쪽 실행으로 대신 그린다", () => {
    const runs = db.prepare(RUNS_IN_GROUP).all("g2", "KR") as unknown as BacktestRun[];
    expect(pickCurveRuns(runs, ["composite", "benchmark"], 3).map((r) => r.strategy)).toEqual([
      "composite", "benchmark",
    ]);
    // 장기 문턱 비교 묶음에는 composite 가 없다 (운영 DB 의 최신 묶음이 실제로 그렇다)
    const longOnly = [
      { id: 10, strategy: "benchmark" }, { id: 11, strategy: "long_q60" }, { id: 12, strategy: "long_q55" },
      { id: 13, strategy: "long_q50" },
    ];
    expect(pickCurveRuns(longOnly, ["composite"], 2).map((r) => r.strategy)).toEqual(["benchmark", "long_q60"]);
    expect(pickCurveRuns([], ["composite"], 2)).toEqual([]);
  });

  it("바스켓 종목 이름을 id 로 찾는다", () => {
    const rows = db.prepare(stocksSql(1)).all(1) as Array<{ name: string }>;
    expect(rows[0].name).toBe("삼성전자");
  });

  it("최신 스트레스 결과와 실행 상태를 읽는다", () => {
    const stress = db.prepare(LATEST_STRESS).get("KR") as { weighting: string; cash_weight: number };
    expect(stress.weighting).toBe("suggested");
    expect(stress.cash_weight).toBe(0.3);
    const runs = db.prepare(RECENT_BATCH_RUNS).all() as unknown as BatchRunRow[];
    expect(runs.map((r) => r.job_name)).toEqual(["backtest", "stress"]);
    const 곧 = new Date("2026-09-17T00:30:00Z");
    expect(isRunning(runs, 곧)).toBe(true);
    expect(isRunning(runs.filter((r) => r.status !== "running"), 곧)).toBe(false);
    expect(stuckRunNote(runs, 곧)).toBeNull();
    // 취소·강제 종료로 남은 running 은 75분 뒤 멈춘 것으로 본다 — 버튼이 영영 잠기지 않는다 (25.581)
    const 나중 = new Date("2026-09-17T01:16:00Z");
    expect(isRunning(runs, 나중)).toBe(false);
    expect(stuckRunNote(runs, 나중)).toContain("backtest(KR)");
  });
});

describe("표시 도우미", () => {
  it("전략 이름은 한국어로, 문턱이 붙은 것은 규칙으로 읽는다", () => {
    expect(strategyLabel("composite")).toBe("종합 점수");
    expect(strategyLabel("composite_no_cost")).toBe("종합 점수 (비용 0)");
    expect(strategyLabel("long_q55")).toBe("장기 신호 문턱 55");
    expect(strategyLabel("처음보는것")).toBe("처음보는것");
  });

  it("팩터 하나짜리만 성과요인분석으로 묶는다", () => {
    expect(strategyKind("composite")).toBe("main");
    expect(strategyKind("benchmark")).toBe("main");
    // 발굴 루프 후보는 성과요인이 아니다 (docs/infra.md 25.443)
    expect(strategyKind("momentum_sector")).toBe("candidate");
    expect(strategyKind("momentum")).toBe("factor");
    expect(strategyKind("value")).toBe("factor");
    expect(strategyKind("long_q60")).toBe("long");
  });

  it("경고는 전략마다 반복되므로 합쳐서 한 번만 보여 준다", () => {
    const runs = db.prepare(RUNS_IN_GROUP).all("g2", "KR") as unknown as BacktestRun[];
    const merged = mergeWarnings(runs);
    expect(merged).toHaveLength(2);
    expect(merged.some((w) => w.includes("생존편향"))).toBe(true);
    // 망가진 경고는 "경고 없음" 이 아니다 — 못 읽었다고 말한다 (25.581. 예전 기대값 [] 는 삼킴을 굳혔다)
    expect(mergeWarnings([{ warnings_json: "{망가진" }])).toEqual(["전략 1개의 경고를 읽지 못했습니다 — 빠진 경고가 있을 수 있습니다"]);
  });

  it("비용 열쇠 이름이 파이썬 Costs 의 필드와 같다", () => {
    // 여기가 어긋나면 화면 비용이 전부 "-" 로 찍힌다. 한 번 그랬다
    const source = readFileSync(join(process.cwd(), "..", "batch", "services", "backtest.py"), "utf-8");
    for (const key of COST_KEYS) {
      expect(source).toContain(`${key}: float`);
    }
    const runs = db.prepare(RUNS_IN_GROUP).all("g2", "KR") as unknown as BacktestRun[];
    expect(costLine(runs[0].costs_json)).toBe("편도 수수료 0.015% · 매도 거래세 0.18% · 편도 슬리피지 0.1%");
    expect(costLine("{망가진")).toContain("읽지 못했습니다");
    // 체결일 세율 (25.783)
    const 시점별 = JSON.stringify({
      commission_pct: 0.015, tax_sell_pct: 0.2, slippage_pct: 0.1,
      tax_schedule: [["0001-01-01", 0.3], ["2025-01-01", 0.15], ["2026-01-01", 0.2]],
    });
    expect(costLine(시점별)).toBe("편도 수수료 0.015% · 매도 거래세 체결일 세율(2025-01~ 0.15% · 2026-01~ 0.2%, 그 전 0.3%) · 편도 슬리피지 0.1%");
  });

  it("지표는 저장된 JSON 에서 꺼내고 없으면 null", () => {
    const m = JSON.parse((db.prepare(RUNS_IN_GROUP).all("g2", "KR") as unknown as BacktestRun[])[0].metrics_json);
    expect(metricNumber(m, "cagr")).toBe(0.12);
    expect(metricNumber(m, "없는지표")).toBeNull();
    expect(metricNumber(null, "cagr")).toBeNull();
  });
});

describe("실행 요청", () => {
  it("입력 기본값은 워크플로 기본값과 같다", () => {
    const parsed = runInputSchema.parse({ market: "KR" });
    expect(parsed).toEqual({ market: "KR", what: "둘다", years: 5, top_n: 20, trend_filter: false, no_pit_universe: false });
    expect(runInputSchema.safeParse({ market: "KR", years: 0 }).success).toBe(false);
    expect(runInputSchema.safeParse({ market: "JP" }).success).toBe(false);
  });

  it("고를 수 있는 항목이 워크플로의 선택지와 같다", () => {
    const workflow = readFileSync(join(process.cwd(), "..", ".github", "workflows", "backtest.yml"), "utf-8");
    expect(workflow).toContain(`options: [${WHAT_OPTIONS.join(", ")}]`);
    // 두 길 모두 워크플로가 읽는다
    expect(workflow).toContain("types: [backtest]");
    expect(workflow).toContain("github.event.client_payload.market");
  });

  it("토큰이 없으면 부르지 않고 이유를 준다", async () => {
    vi.stubEnv("GH_DISPATCH_TOKEN", "");
    const fake = vi.fn();
    const r = await requestBacktest({ market: "KR", what: "둘다", years: 5, top_n: 20 }, fake as unknown as typeof fetch);
    expect(r.dispatched).toBe(false);
    expect(fake).not.toHaveBeenCalled();
  });

  it("workflow_dispatch 로 파라미터를 문자열로 넘긴다", async () => {
    vi.stubEnv("GH_DISPATCH_TOKEN", "x");
    vi.stubEnv("GH_REPO", "owner/repo");
    const fake = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const r = await requestBacktest({ market: "US", what: "스트레스", years: 3, top_n: 15 }, fake as unknown as typeof fetch);
    expect(r).toEqual({ dispatched: true, via: "workflow_dispatch" });
    const [url, init] = fake.mock.calls[0];
    expect(url).toBe(`https://api.github.com/repos/owner/repo/actions/workflows/${WORKFLOW_FILE}/dispatches`);
    const body = JSON.parse(init.body);
    expect(body.ref).toBe(WORKFLOW_REF);
    expect(body.inputs).toEqual({ what: "스트레스", market: "US", years: "3", top_n: "15" });
  });

  it("추세 필터·시점 유니버스 끄기는 켠 것만 보낸다 (문자열 false 는 워크플로에서 참이다)", async () => {
    vi.stubEnv("GH_DISPATCH_TOKEN", "t");
    vi.stubEnv("GH_REPO", "o/r");
    const calls: Array<{ url: string; body: string }> = [];
    const fake = async (url: string, init: { body: string }) => {
      calls.push({ url, body: init.body });
      return { status: 204 };
    };
    await requestBacktest(
      { market: "KR", what: "둘다", years: 5, top_n: 20, trend_filter: true, no_pit_universe: false },
      fake as unknown as typeof fetch,
    );
    const body = JSON.parse(calls[0].body);
    expect(body.inputs.trend_filter).toBe("true");
    expect("no_pit_universe" in body.inputs).toBe(false);
    // 워크플로가 두 플래그를 읽는다
    const workflow = readFileSync(join(process.cwd(), "..", ".github", "workflows", "backtest.yml"), "utf8");
    expect(workflow).toContain("client_payload.trend_filter");
    expect(workflow).toContain("client_payload.no_pit_universe");
  });

  it("권한이 모자라면 repository_dispatch 로 한 번 더 시도한다", async () => {
    vi.stubEnv("GH_DISPATCH_TOKEN", "x");
    vi.stubEnv("GH_REPO", "owner/repo");
    const fake = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 403 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const r = await requestBacktest({ market: "KR", what: "둘다", years: 5, top_n: 20 }, fake as unknown as typeof fetch);
    expect(r).toEqual({ dispatched: true, via: "repository_dispatch" });
    const [url, init] = fake.mock.calls[1];
    expect(url).toBe("https://api.github.com/repos/owner/repo/dispatches");
    const body = JSON.parse(init.body);
    expect(body.event_type).toBe("backtest");
    expect(body.client_payload.years).toBe("5");
  });

  it("권한 문제가 아니면 두 번째 길을 부르지 않는다", async () => {
    vi.stubEnv("GH_DISPATCH_TOKEN", "x");
    vi.stubEnv("GH_REPO", "owner/repo");
    const fake = vi.fn().mockResolvedValue(new Response(null, { status: 422 }));
    const r = await requestBacktest({ market: "KR", what: "둘다", years: 5, top_n: 20 }, fake as unknown as typeof fetch);
    expect(r.dispatched).toBe(false);
    expect(fake).toHaveBeenCalledTimes(1);
  });

  it("둘 다 안 되면 권한을 확인하라고 알린다", async () => {
    vi.stubEnv("GH_DISPATCH_TOKEN", "x");
    vi.stubEnv("GH_REPO", "owner/repo");
    const fake = vi.fn().mockResolvedValue(new Response(null, { status: 404 }));
    const r = await requestBacktest({ market: "KR", what: "둘다", years: 5, top_n: 20 }, fake as unknown as typeof fetch);
    expect(r.dispatched).toBe(false);
    expect(r.reason).toContain("권한");
    expect(fake).toHaveBeenCalledTimes(2);
  });
});

describe("배당 문구는 나라마다 (docs/infra.md 25.244)", () => {
  it("미국은 총수익, 국내는 가격 수익", async () => {
    const { dividendNote } = await import("@/lib/backtest");
    expect(dividendNote("US")).toContain("배당 재투자가 들어 있습니다");
    expect(dividendNote("KR")).toContain("넣지 않았습니다");
  });
});

describe("요청 직후 버튼 잠금 (25.585)", () => {
  const 행 = (started_at: string) => ({ id: 1, job_name: "backtest", market: "KR", status: "running", started_at, finished_at: null, step_log: null, error_text: null });
  const 요청 = Date.parse("2026-09-28T00:00:00Z");
  it("기록이 안 보이는 동안 잠그고, 기록이 보이거나 5분이 지나면 푼다", () => {
    expect(awaitingRunner(요청, [], 요청 + 60_000)).toBe(true);
    expect(awaitingRunner(요청, [행("2026-09-27T23:00:00Z")], 요청 + 60_000), "옛 기록은 이번 요청이 아니다").toBe(true);
    expect(awaitingRunner(요청, [행("2026-09-28T00:01:30Z")], 요청 + 120_000)).toBe(false);
    expect(awaitingRunner(요청, [], 요청 + 6 * 60_000)).toBe(false);
    expect(awaitingRunner(null, [], 요청)).toBe(false);
  });
});

describe("버튼 잠금 교차검증 (25.589)", () => {
  const 요청 = Date.parse("2026-09-28T00:00:00Z");
  const 행 = (started_at: string, status: string, finished_at: string | null) => ({ id: 1, job_name: "stress", market: "KR", status, started_at, finished_at, step_log: null, error_text: null });
  it("요청 전에 끝난 앞 실행은 1분 여유 안이어도 이번 기록이 아니다", () => {
    expect(awaitingRunner(요청, [행("2026-09-27T23:59:10Z", "success", "2026-09-27T23:59:40Z")], 요청 + 30_000)).toBe(true);
    expect(awaitingRunner(요청, [행("2026-09-28T00:01:00Z", "running", null)], 요청 + 90_000)).toBe(false);
  });
  it("성공한 백테스트 단계 뒤 2분은 계속 읽는다 — 실패나 스트레스 끝은 아니다 (25.594)", () => {
    const 백 = (status: string) => ({ ...행("2026-09-28T00:00:00Z", status, "2026-09-28T00:05:00Z"), job_name: "backtest" });
    expect(recentlyFinished([백("success")], Date.parse("2026-09-28T00:06:00Z"))).toBe(true);
    expect(recentlyFinished([백("success")], Date.parse("2026-09-28T00:08:00Z"))).toBe(false);
    expect(recentlyFinished([백("failed")], Date.parse("2026-09-28T00:06:00Z"))).toBe(false);
    expect(recentlyFinished([행("2026-09-28T00:00:00Z", "success", "2026-09-28T00:05:00Z")], Date.parse("2026-09-28T00:06:00Z"))).toBe(false);
  });
});

it("단계 사이 틈에도 실행 버튼을 잠근다 (25.592)", async () => {
  const { readFileSync } = await import("node:fs");
  expect(readFileSync("components/BacktestView.tsx", "utf8")).toContain("running={running || settling}");
});


describe("노출 표시 (25.786)", () => {
  it("투자한 달·평균 보유·첫 투자일, 없으면 -", async () => {
    const { exposureText } = await import("@/lib/backtest");
    expect(exposureText({ invested_share: 0.5417, avg_holdings: 12.345, first_invested: "2025-07-31" }))
      .toEqual({ invested: "54%", holdings: "12.34종목", first: "2025-07-31" });
    // 배치 경고와 같이 내려 적는다 — 2.96 이 "3.0" 으로 보이지 않고, 0.57·1.15 가 한 단위 더 내려가지 않는다 (25.790)
    expect(exposureText({ invested_share: 0.57, avg_holdings: 2.96 })).toMatchObject({ invested: "57%", holdings: "2.96종목" });
    expect(exposureText({ invested_share: 0.29, avg_holdings: 1.15 })).toMatchObject({ invested: "29%", holdings: "1.15종목" });
    expect(exposureText({ cagr: 0.1 })).toEqual({ invested: "-", holdings: "-", first: null });
    expect(exposureText(null)).toEqual({ invested: "-", holdings: "-", first: null });
  });
});

describe("비용 기본값 표시 — 거래세 법정 표 (25.787)", () => {
  it("법정 표를 '아직 실측 아닌 기본값' 으로 부르지 않고, 설정이 있어도 지난 연도는 법정 표라고 말한다", async () => {
    const { costDefaultsNote } = await import("@/lib/backtest");
    const 표 = [["0001-01-01", 0.3], ["2025-01-01", 0.15], ["2026-01-01", 0.2]];
    expect(costDefaultsNote(JSON.stringify({ defaults: ["commission", "tax", "slippage"], tax_schedule: 표 })))
      .toBe("(기본값·아직 실측 아님: 수수료, 슬리피지 · 거래세는 법정 세율 표)");
    expect(costDefaultsNote(JSON.stringify({ defaults: [], tax_schedule: 표 })))
      .toBe("(모두 설정값 · 지난 연도 거래세는 법정 세율 표, 올해분만 설정값)");
    // 미국(표 없음)·옛 실행은 그대로
    expect(costDefaultsNote(JSON.stringify({ defaults: ["commission", "slippage"], tax_schedule: [] })))
      .toBe("(기본값·아직 실측 아님: 수수료, 슬리피지)");
  });
});

describe("기본 묶음과 옛 판 (25.788, 백테스트 감사 #2·#3)", () => {
  function 새_db(): DatabaseSync {
    const d = new DatabaseSync(":memory:");
    for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
      d.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
    }
    return d;
  }
  const 넣기 = (d: DatabaseSync, id: number, strategy: string, group: string, at: string, warnings: string[]) =>
    d.prepare(
      `INSERT INTO backtest_runs (id, market, strategy, start_date, end_date, top_n, rebalance, costs_json, rebalances,
        final_equity, turnover_avg, excess_cagr, win_rate, metrics_json, warnings_json, group_id, calc_version, created_at)
       VALUES (?, 'KR', ?, '2021-09-01', '2026-09-01', 20, 'monthly', '{}', 60, 1.5, 0.3, 0.02, 0.5, '{}', ?, ?, 3, ?)`,
    ).run(id, strategy, JSON.stringify(warnings), group, at);

  it("비교용(시점 유니버스 끔·추세 필터 켬)·장기 문턱만 돈 묶음이 뒤에 와도 규칙 판단용 묶음을 보인다", () => {
    const d = 새_db();
    넣기(d, 1, "composite", "본", "2026-09-17T00:00:00Z", ["시점 유니버스: …"]);
    넣기(d, 2, "benchmark", "본", "2026-09-17T00:00:00Z", []);
    넣기(d, 3, "composite", "끔", "2026-09-18T00:00:00Z", [`${NO_PIT_MARK}. 현재 유니버스를 전 기간에 썼다`]);
    넣기(d, 4, "composite", "추세", "2026-09-19T00:00:00Z", [`${TREND_ON_MARK}: 지수 < 200일선이면…`]);
    넣기(d, 5, "long_q60", "장기", "2026-09-20T00:00:00Z", []);
    넣기(d, 6, "benchmark", "장기", "2026-09-20T00:00:00Z", []);
    expect((d.prepare(LATEST_GROUP).get("KR") as { group_id: string }).group_id).toBe("본");
    const 목록 = d.prepare(RECENT_GROUPS).all("KR") as Array<Parameters<typeof groupTags>[0] & { group_id: string }>;
    expect(Object.fromEntries(목록.map((g) => [g.group_id, groupTags(g)]))).toEqual({
      본: "", 끔: " · 비교용 · 시점 유니버스 끔", 추세: " · 추세 필터 켬", 장기: " · 장기 문턱만",
    });
  });

  it("기간·상위 N 이 기본이 아닌 묶음이 뒤에 와도 대표가 되지 않고, 목록에 비교용으로 붙는다 (25.928)", () => {
    const d = 새_db();
    넣기(d, 1, "composite", "본", "2026-09-17T00:00:00Z", ["시점 유니버스: …"]);
    넣기(d, 2, "composite", "짧음", "2026-09-18T00:00:00Z", [`${NOT_DEFAULT_MARK} (비교용): 기간 2년 · 상위 20개`]);
    넣기(d, 3, "composite", "옛50", "2026-09-19T00:00:00Z", []);
    d.prepare("UPDATE backtest_runs SET top_n = 50 WHERE group_id = '옛50'").run(); // 표시가 생기기 전 실행
    expect((d.prepare(LATEST_GROUP).get("KR") as { group_id: string }).group_id).toBe("본");
    const 목록 = d.prepare(RECENT_GROUPS).all("KR") as Array<Parameters<typeof groupTags>[0] & { group_id: string }>;
    expect(Object.fromEntries(목록.map((g) => [g.group_id, groupTags(g)]))).toEqual({
      본: "", 짧음: " · 비교용 · 기본 파라미터 아님", 옛50: " · 비교용 · 기본 파라미터 아님",
    });
  });

  it("규칙 판단용 묶음이 없으면 가장 최근 묶음", () => {
    const d = 새_db();
    넣기(d, 1, "composite", "끔", "2026-09-18T00:00:00Z", [`${NO_PIT_MARK}.`]);
    넣기(d, 2, "composite", "추세", "2026-09-19T00:00:00Z", [`${TREND_ON_MARK}:`]);
    expect((d.prepare(LATEST_GROUP).get("KR") as { group_id: string }).group_id).toBe("추세");
  });

  it("표시 문구와 판 번호는 파이썬과 같다", () => {
    const 잡 = readFileSync(join(process.cwd(), "..", "batch", "jobs", "backtest.py"), "utf-8");
    expect(잡).toContain(`WARN_NO_HISTORICAL_UNIVERSE = "${NO_PIT_MARK}`);
    expect(잡).toContain(`WARN_TREND_ON = "${TREND_ON_MARK}`);
    expect(잡).toContain(`WARN_NOT_DEFAULT_PARAMS = "${NOT_DEFAULT_MARK}`);
    const 기본N = readFileSync(join(process.cwd(), "..", "batch", "services", "backtest.py"), "utf-8");
    expect(기본N).toMatch(new RegExp(`^DEFAULT_TOP_N = ${DEFAULT_TOP_N}$`, "m"));
    const 엔진 = readFileSync(join(process.cwd(), "..", "batch", "services", "backtest.py"), "utf-8");
    expect(엔진).toMatch(new RegExp(`^CALC_VERSION = ${ENGINE_VERSION}$`, "m"));
    const 점수 = readFileSync(join(process.cwd(), "..", "batch", "services", "scoring.py"), "utf-8");
    expect(점수).toMatch(new RegExp(`^CALC_VERSION = ${SCORING_VERSION}$`, "m"));
  });

  it("옛 판 결과에 띠를 붙인다", () => {
    expect(staleVersionNote({ calc_version: ENGINE_VERSION, scoring_calc_version: SCORING_VERSION })).toBeNull();
    expect(staleVersionNote({ calc_version: 1, scoring_calc_version: null })).toContain("엔진 1 → 지금");
    expect(staleVersionNote({ calc_version: ENGINE_VERSION, scoring_calc_version: SCORING_VERSION - 1 })).toContain("점수");
    // 3판은 국내 거래세만 바꿨다 — 미국 3판은 3판으로는 낡지 않았지만 4판(마지막 날, 모든 시장)으로는 낡았다 (25.789·25.795)
    expect(staleVersionNote({ calc_version: 3, scoring_calc_version: SCORING_VERSION, market: "US" })).toContain("엔진 3");
    expect(staleVersionNote({ calc_version: ENGINE_VERSION, scoring_calc_version: SCORING_VERSION, market: "US" })).toBeNull();
    expect(staleVersionNote({ calc_version: 2, scoring_calc_version: SCORING_VERSION, market: "KR" })).toContain("엔진 2");
    expect(staleVersionNote({ calc_version: 1, scoring_calc_version: SCORING_VERSION, market: "US" })).toContain("엔진 1");
  });
});
