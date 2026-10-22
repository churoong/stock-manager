import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { KisRealized, headline } from "@/components/PaperRealized";
import { BROKER_KEYS } from "@/lib/paper";
import {
  KIS_REALIZED,
  KIS_REALIZED_LEGACY,
  PAPER_REALIZED,
  PAPER_REALIZED_LEGACY,
  periodRange,
  realizedReport,
  validRange,
  type RealizedRow,
} from "@/lib/paperRealized";

// docs/infra.md 25.1693 — 가상모의투자 기간 실현손익·일자별 실현손익(2026-10-22 사용자 지시)
const 줄 = (id: number, d: string, sid: number, v: number | null, cur = "KRW", fx: number | null = 0): RealizedRow => ({
  id, fill_date: d, stock_id: sid, ticker: `T${sid}`, name: `종목${sid}`, currency: cur, quantity: 1, price: 1,
  fee: 1, tax: 2, slippage: 0, realized_krw: v, realized_fx_krw: fx,
});

describe("기간", () => {
  it("이번 달·지난 달·최근 30일·올해·전체", () => {
    expect(periodRange("this_month", "2026-10-22")).toEqual(["2026-10-01", "2026-10-22"]);
    expect(periodRange("last_month", "2026-10-22")).toEqual(["2026-09-01", "2026-09-30"]);
    expect(periodRange("last_month", "2027-01-05")).toEqual(["2026-12-01", "2026-12-31"]);
    expect(periodRange("last_month", "2028-03-10")).toEqual(["2028-02-01", "2028-02-29"]); // 윤년
    expect(periodRange("d30", "2026-10-22")).toEqual(["2026-09-23", "2026-10-22"]);
    expect(periodRange("this_year", "2026-10-22")).toEqual(["2026-01-01", "2026-10-22"]);
    expect(periodRange("all", "2026-10-22")[0] <= "2026-10-15").toBe(true);
  });
  it("직접 — 꼴이 틀리면 오늘 하루", () => {
    expect(periodRange("custom", "2026-10-22", { from: "2026-10-01", to: "2026-10-10" })).toEqual(["2026-10-01", "2026-10-10"]);
    expect(periodRange("custom", "2026-10-22", { from: "10월", to: "x" })).toEqual(["2026-10-22", "2026-10-22"]);
    expect(validRange("2026-10-01", "2026-10-10")).toBe(true);
    expect(validRange("2026-10-10", "2026-10-01")).toBe(false);
    expect(validRange("2026-10-1", "2026-10-10")).toBe(false);
  });
});

describe("기간 실현손익", () => {
  const rows = [
    줄(3, "2026-10-16", 1, -5_000),
    줄(1, "2026-10-15", 1, 10_000),
    줄(2, "2026-10-15", 2, 3_000, "USD", 1_200),
    줄(4, "2026-10-16", 2, 2_000, "USD", -500),
    줄(5, "2026-10-17", 3, null), // 팔 때 장부 보유 없음 — 합에 넣지 않는다
  ];
  const r = realizedReport(rows);
  it("합·이긴 수·환차와 주가 몫", () => {
    expect(r.n).toBe(4);
    expect(r.realized).toBe(10_000);
    expect(r.fx).toBe(700);
    expect(r.price).toBe(9_300);
    expect([r.wins, r.losses, r.unknown, r.fxUnknown]).toEqual([3, 1, 1, 0]);
    expect(r.hasForeign).toBe(true);
    expect(r.costs).toEqual({ KRW: 9, USD: 6 });
  });
  it("일자별 — 날짜 순, 누적", () => {
    // 실현손익을 모르는 매도의 날도 표에 남는다(25.1722) — 합·누적에는 0
    expect(r.byDay.map((d) => [d.key, d.n, d.unknown, d.realized, d.fx, d.cumulative])).toEqual([
      ["2026-10-15", 2, 0, 13_000, 1_200, 13_000],
      ["2026-10-16", 2, 0, -3_000, -500, 10_000],
      ["2026-10-17", 0, 1, 0, 0, 10_000],
    ]);
  });
  it("종목별 — 실현손익 큰 순", () => {
    expect(r.byStock.map((s) => [s.label, s.n, s.unknown, s.realized])).toEqual([
      ["종목1 (T1)", 2, 0, 5_000],
      ["종목2 (T2)", 2, 0, 5_000],
      ["종목3 (T3)", 0, 1, 0],
    ]);
  });
  it("환차 몫을 아직 모르는 해외 매도가 있으면 주가 몫도 모름", () => {
    const x = realizedReport([줄(1, "2026-10-15", 2, 3_000, "USD", null), 줄(2, "2026-10-15", 2, 1_000, "USD", 200)]);
    expect(x.fxUnknown).toBe(1);
    // 일자별·종목별 묶음도 모르는 수를 센다(25.1695 — 아는 것만 더한 값을 다 아는 것처럼 보이지 않게)
    expect([x.byDay[0].fx, x.byDay[0].fxUnknown, x.byStock[0].fxUnknown]).toEqual([200, 1, 1]);
    expect(Number.isNaN(x.price)).toBe(true);
    expect(realizedReport([]).byDay).toEqual([]);
  });
});

// 질의는 실제 마이그레이션을 적용한 SQLite 에 돌린다(25.1693·25.1694)

function 디비(): DatabaseSync {
  const db = new DatabaseSync(":memory:");
  const 폴더 = join(process.cwd(), "..", "migrations");
  for (const f of readdirSync(폴더).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(폴더, f), "utf8"));
  db.exec("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at) VALUES (1, 'AAPL', 'NASDAQ', 'US', '애플', 'USD', 'active', 't', 't')");
  return db;
}

describe("기간 실현손익 질의", () => {
  it("가상모의투자 — 그 계좌·그 기간의 매도만, 환차 칸", () => {
    const db = 디비();
    for (const [id, side, d, 실현, 환차] of [[1, "buy", "2026-10-15", null, null], [2, "sell", "2026-10-16", 237_160, 100_100], [3, "sell", "2026-11-02", 5, 0]] as const) {
      db.prepare("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency, decided_on, fill_session, reason, rule_version, created_at) VALUES (?, 'A', 'US', 1, ?, 'market', 10, 'USD', ?, ?, 'x', 1, 't')").run(id, side, d, d);
      db.prepare("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency, fx_rate, fee, tax, slippage, cash_krw, created_at, realized_krw, realized_fx_krw) VALUES (?, 'A', 1, ?, ?, 100, 10, 'USD', 1300, 1, 0, 0, 1, 't', ?, ?)").run(id, side, d, 실현, 환차);
    }
    const rows = db.prepare(PAPER_REALIZED).all("A", "2026-10-01", "2026-10-31") as Array<Record<string, unknown>>;
    expect(rows.map((r) => [r.fill_date, r.name, r.realized_krw, r.realized_fx_krw])).toEqual([["2026-10-16", "애플", 237_160, 100_100]]);
    expect(db.prepare(PAPER_REALIZED_LEGACY).all("A", "2026-10-01", "2026-10-31")).toHaveLength(1);
    expect(db.prepare(PAPER_REALIZED).all("B", "2026-10-01", "2026-10-31")).toHaveLength(0);
  });

  it("KIS모의투자 — 체결된 매도만, 통화는 시장대로", () => {
    const db = 디비();
    db.exec("INSERT INTO paper_accounts (id, name, rule_version, created_at) SELECT 'H', 'H', 1, 't' WHERE NOT EXISTS (SELECT 1 FROM paper_accounts WHERE id = 'H')");
    const 넣기 = db.prepare("INSERT INTO paper_broker_orders (broker, account_id, market, stock_id, code, side, order_type, quantity, trade_date, reason, status, filled_qty, avg_price, source, created_at, realized_krw, realized_fx_krw) VALUES (?, 'H', 'US', 1, 'AAPL', ?, 'market', 10, ?, '베낌', ?, ?, 110, 'kis_vts', 't', ?, ?)");
    넣기.run(BROKER_KEYS.US, "sell", "2026-10-20", "filled", 10, 240_000, 100_000);
    넣기.run(BROKER_KEYS.US, "sell", "2026-10-21", "unfilled", 0, null, null);
    넣기.run(BROKER_KEYS.US, "buy", "2026-10-15", "filled", 10, null, null);
    const rows = db.prepare(KIS_REALIZED).all("USD", BROKER_KEYS.US, "2026-10-01", "2026-10-31") as Array<Record<string, unknown>>;
    expect(rows.map((r) => [r.fill_date, r.currency, r.quantity, r.realized_krw, r.realized_fx_krw])).toEqual([["2026-10-20", "USD", 10, 240_000, 100_000]]);
    expect((db.prepare(KIS_REALIZED_LEGACY).all("USD", BROKER_KEYS.US, "2026-10-01", "2026-10-31")[0] as Record<string, unknown>).realized_krw).toBeNull();
  });

  it("KIS 화면은 비용 칸 없이 '비용 전' 을 밝힌다", () => {
    const html = renderToStaticMarkup(createElement(KisRealized, { market: "US" }));
    expect(html).toContain("수수료·세금은 기록에 없어 빠집니다");
    expect(html).toContain("이번 달");
    // 머리 칸에도(25.1698) — 비용 전 값을 비용 뺀 가상과 나란히 읽지 않게
    expect([headline(false), headline(true)]).toEqual(["기간 실현손익(비용 전)", "기간 실현손익"]);
  });
});
