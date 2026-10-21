/**
 * 연구실 (docs/desk.md 5장, docs/infra.md 25.1506) — 질의는 실제 마이그레이션을 적용한 SQLite 에 돌린다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeEach, describe, expect, it } from "vitest";
import {
  JOURNEY_BROKER_SQL,
  JOURNEY_DECISIONS_SQL,
  JOURNEY_ORDERS_SQL,
  JOURNEY_SIGNALS_SQL,
  JOURNEY_STOCK_SQL,
  LAB_ATTRIBUTION_SQL,
  LAB_HOLDING_SQL,
  JOURNEY_LIMIT,
  holdingPoints,
  journeyCut,
  journeyEvents,
  weightSeries,
  type JourneyRaw,
} from "@/lib/lab";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
  db.exec("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at) VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')");
});

const plan = (sql: string, ...args: Array<string | number>) =>
  (db.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...args) as Array<{ detail: string }>).map((p) => p.detail).join(" | ");

describe("종목 여정 질의", () => {
  it("다섯 원장을 종목 색인으로 읽는다(표 전체를 훑지 않는다)", () => {
    expect(plan(JOURNEY_DECISIONS_SQL, 1)).toMatch(/idx_paper_decisions_stock/);
    expect(plan(JOURNEY_ORDERS_SQL, 1)).toMatch(/idx_paper_orders_stock/);
    expect(plan(JOURNEY_BROKER_SQL, 1)).toMatch(/idx_paper_broker_orders_stock/);
    expect(plan(JOURNEY_SIGNALS_SQL, 1)).not.toMatch(/SCAN signals\b(?! USING)/);
    expect((db.prepare(JOURNEY_STOCK_SQL).all("005930") as Array<{ name: string }>)[0].name).toBe("삼성전자");
  });

  it("추천 → 결정 → 주문 → 가상 체결 → KIS, 최근 것 먼저", () => {
    db.exec(`INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency, tranche_plan, size_reduction, rationale_text, rationale_data, calc_version, created_at)
      VALUES (1, '2026-10-15', 'short', '기술적 추세', 9700, 9900, 'KRW', '[]', 0, 'r', '{}', 1, 't')`);
    db.exec(`INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ret_20d, bench_20d, created_at) VALUES
      ('H', 'KR', '2026-10-15', 1, 'buy', '두 곳 이상 추천', 3.1, 1.2, 't'), ('A', 'KR', '2026-10-15', 1, 'skip', '점수 미달', NULL, NULL, 't')`);
    db.exec(`INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency, decided_on, fill_session, reason, rule_version, tranche, status, closed_on, created_at)
      VALUES (7, 'H', 'KR', 1, 'buy', 'limit', 9800, 3, 'KRW', '2026-10-15', '2026-10-16', '배분', 1, 1, 'filled', '2026-10-16', 't'),
             (8, 'H', 'KR', 1, 'buy', 'limit', 9600, 2, 'KRW', '2026-10-15', '2026-10-16', '배분', 1, 2, 'expired', '2026-10-16', 't')`);
    db.exec(`INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency, fx_rate, fee, tax, slippage, cash_krw, created_at)
      VALUES (7, 'H', 1, 'buy', '2026-10-16', 9800, 3, 'KRW', 1, 0, 0, 0, -29400, 't')`);
    db.exec(`INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side, order_type, limit_price, quantity, trade_date, reason, status, filled_qty, avg_price, source, created_at)
      VALUES ('kis_vts', 7, 'H', 'KR', 1, '005930', 'buy', 'limit', 9800, 3, '2026-10-16', '베낌', 'filled', 3, 9790, 'kis_vts', 't')`);
    const raw = {
      signals: db.prepare(JOURNEY_SIGNALS_SQL).all(1),
      decisions: db.prepare(JOURNEY_DECISIONS_SQL).all(1),
      orders: db.prepare(JOURNEY_ORDERS_SQL).all(1),
      broker: db.prepare(JOURNEY_BROKER_SQL).all(1),
    } as unknown as JourneyRaw;
    const e = journeyEvents(raw);
    expect(e.map((x) => [x.day, x.step, x.account])).toEqual([
      ["2026-10-16", "KIS", "H"],
      ["2026-10-16", "가상 체결", "H"],
      ["2026-10-16", "안 참", "H"],
      ["2026-10-15", "주문", "H"],
      ["2026-10-15", "주문", "H"],
      ["2026-10-15", "결정", "A"],
      ["2026-10-15", "결정", "H"],
      ["2026-10-15", "추천", null],
    ]);
    const 글 = e.map((x) => x.text).join(" / ");
    expect(글).toContain("베낌 매수 3주 · 체결 · 체결 3주 @ 9,790원");
    expect(글).toContain("매수 3주 @ 9,800원");
    expect(글).toContain("사기로 — 두 곳 이상 추천 · 20일 뒤 +3.1% (지수 +1.2%)");
    expect(글).toContain("매수 2주 · 지정가 9,600원 · 2차 · 2026-10-16 장");
    expect(글).toContain("단기 추천 · 기술적 추세 · 매수 구간 9,700원~9,900원");
  });
});

describe("성과 분해·지주사 가중치", () => {
  it("계좌마다 마지막 평가의 성과 분해", () => {
    const ins = db.prepare(
      "INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw, pnl_krw, twr_index, positions_json, recon_ok, updated_at, attribution_json) VALUES (?, ?, 1, 1, 0, 1, 0, 1, '[]', 1, 't', ?)",
    );
    ins.run("A", "2026-10-15", '{"horizon": 20}');
    ins.run("A", "2026-10-16", '{"horizon": 21}');
    ins.run("B", "2026-10-16", null);
    // 오늘 평가 행에 성과 분해가 아직 안 적혔으면(국내 배치는 단계 끝에 적는다·실패한 날) 적힌 어제 것 — 25.1630
    ins.run("A", "2026-10-17", null);
    const rows = db.prepare(LAB_ATTRIBUTION_SQL).all() as Array<{ account_id: string; as_of: string; attribution_json: string | null }>;
    expect(rows.map((r) => [r.account_id, r.as_of, r.attribution_json])).toEqual([
      ["A", "2026-10-16", '{"horizon": 21}'],
      ["B", "2026-10-16", null],
    ]);
  });

  it("운용 요약에서 시장별 지주사 기록 — 이어받은 같은 칸은 한 번, 그래프는 회사별", () => {
    const h = (w: Record<string, number>) => ({ weights: w, samples: { A: 3 }, recommended: 5, eligible: 2, chosen: 2 });
    const ins = db.prepare("INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES (?, ?, 't')");
    ins.run("2026-10-16", JSON.stringify({ markets: { KR: { run_at: "2026-10-15T23:40:00Z", holding: h({ A: 1.0, C: 1.0 }) } } }));
    // 10-17 줄은 KR 칸을 이어받았다(같은 run_at) + 새 US 칸
    ins.run("2026-10-17", JSON.stringify({ markets: { KR: { run_at: "2026-10-15T23:40:00Z", holding: h({ A: 1.0, C: 1.0 }) }, US: { run_at: "2026-10-17T13:20:00Z", holding: h({ A: 0.9 }) } } }));
    ins.run("2026-10-20", JSON.stringify({ markets: { KR: { run_at: "2026-10-19T23:40:00Z", holding: h({ A: 1.2, C: 0.8 }) } } }));
    const p = holdingPoints(db.prepare(LAB_HOLDING_SQL).all() as Array<{ kr: string | null; kr_run: string | null; us: string | null; us_run: string | null }>);
    expect(p.map((x) => [x.market, x.run_at])).toEqual([
      ["KR", "2026-10-15T23:40:00Z"],
      ["US", "2026-10-17T13:20:00Z"],
      ["KR", "2026-10-19T23:40:00Z"],
    ]);
    const s = weightSeries(p, "KR");
    expect(s.map((x) => x.label)).toEqual(["A 가중치", "C 가중치"]);
    expect(s[0].points).toEqual([
      { date: "2026-10-16", value: 1.0 },
      { date: "2026-10-20", value: 1.2 },
    ]);
    expect(weightSeries(p, "US")[0].points).toEqual([{ date: "2026-10-17", value: 0.9 }]);
  });
});

describe("검토 반영 (25.1509)", () => {
  it("꽉 찬 원장들의 가장 오래된 날 가운데 가장 늦은 날에서 자른다", () => {
    const 결정 = Array.from({ length: JOURNEY_LIMIT }, (_, i) => ({ id: i, account_id: "A", market: "KR", day: `2026-10-${String(20 - (i % 5)).padStart(2, "0")}`, kind: "skip", reason: "r", ret_20d: null, bench_20d: null }));
    const 주문 = [{ id: 1, account_id: "H", market: "KR", day: "2026-08-01", side: "buy", order_type: "limit", limit_price: 1, quantity: 1, currency: "KRW", fill_session: "2026-08-02", tranche: 1, status: "open", closed_on: null, close_note: null, fill_date: null, fill_price: null, fill_qty: null }];
    const raw = { signals: [], decisions: 결정, orders: 주문, broker: [] } as unknown as JourneyRaw;
    expect(journeyCut(raw)).toBe("2026-10-16"); // 결정 원장만 꽉 참 — 그 가장 오래된 날
    expect(journeyCut({ ...raw, decisions: 결정.slice(0, 10) })).toBeNull(); // 꽉 찬 원장이 없으면 자르지 않는다
    expect(journeyEvents(raw).some((e) => e.day === "2026-08-01")).toBe(true); // 자르는 것은 화면 — 사건 자체는 그대로
  });

  it("KIS 줄은 원장의 계좌·시장 통화로", () => {
    const raw = {
      signals: [], decisions: [], orders: [],
      broker: [{ id: 1, account_id: "B", market: "US", day: "2026-10-16", side: "buy", quantity: 2, order_type: "limit", limit_price: 180, status: "filled", filled_qty: 2, avg_price: 182.5, message: null, reason: "베낌" }],
    } as unknown as JourneyRaw;
    const e = journeyEvents(raw)[0];
    expect(e.account).toBe("B");
    expect(e.text).toContain("@ $182.50");
  });

  it("같은 티커 후보는 활성 먼저, 시장·상태와 함께", () => {
    db.exec("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at) VALUES (2, '247540', 'KOSDAQ', 'KR', '옛 행', 'KRW', 'excluded', 't', 't'), (3, '247540', 'KOSPI', 'KR', '새 행', 'KRW', 'active', 't', 't')");
    const r = db.prepare(JOURNEY_STOCK_SQL).all("247540") as Array<{ id: number; market: string; status: string }>;
    expect(r.map((x) => [x.id, x.market, x.status])).toEqual([
      [3, "KOSPI", "active"],
      [2, "KOSDAQ", "excluded"],
    ]);
  });
});
