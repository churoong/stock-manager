/**
 * KIS모의투자 웹 보기 A (docs/paper.md 15.7, docs/infra.md 25.1496). 질의는 실제 마이그레이션을 적용한 SQLite 에 돌린다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeEach, describe, expect, it } from "vitest";
import { kisSeries } from "@/components/KisPanel";
import { KIS_LATEST, KIS_NAMES, KIS_ORDERS, KIS_TOTALS, amountText, parseHoldings, perfLines, priceText, statusText } from "@/lib/kisPaper";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
  db.exec("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at) VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')");
  const ins = db.prepare(
    "INSERT INTO paper_broker_daily (broker, as_of, account_id, cash_krw, total_krw, holdings_json, diff_json, source, fetched_at, summary_json) VALUES (?, ?, 'H', ?, ?, ?, '{}', 'kis_vts', 't', ?)",
  );
  // 예전 줄(배열 보유·account 없음)과 새 줄(객체 보유·account·perf)이 섞여 있다
  ins.run("kis_vts", "2026-10-16", 1_000_000, 1_000_000, JSON.stringify([[1, 3, 9800]]), JSON.stringify({ n: 0 }));
  ins.run("kis_vts", "2026-10-19", 900_000, 1_050_000, JSON.stringify([{ id: 1, code: "005930", qty: 3, avg: 9800, price: 10000, value: 30000, pnl: 600 }]),
    JSON.stringify({ n: 0, account: { currency: "KRW", cash: 900000, holdings_value: 150000, total: 1_050_000 }, account_krw: { kr_total: 1_050_000, value_usd: 110, cash_usd: 0, usd_krw: 1000, total_krw: 1_160_000 } }));
  ins.run("kis_vts_us", "2026-10-16", null, null, "[]", JSON.stringify({ n: 0, account: { currency: "USD", cash: 100000, holdings_value: 0, total: 100000 } }));
  db.exec(
    "INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side, order_type, limit_price, quantity, trade_date, reason, status, source, created_at) VALUES ('kis_vts', NULL, 'H', 'KR', 1, '005930', 'buy', 'limit', 9800, 3, '2026-10-19', '베낌', 'filled', 'kis_vts', 't')",
  );
});

describe("KIS모의투자 질의", () => {
  it("마지막 줄·총액 추이(시장 통화, 예전 줄은 total_krw)·거래내역·이름", () => {
    const r = db.prepare(KIS_LATEST).get("kis_vts") as { as_of: string; holdings_json: string };
    expect(r.as_of).toBe("2026-10-19");
    // 계좌 전체 원화 총액은 국내 배치 줄의 account_krw 에만 — 없는 날은 null
    expect(db.prepare(KIS_TOTALS).all("kis_vts", "2026-01-01")).toEqual([
      { as_of: "2026-10-16", total: null },
      { as_of: "2026-10-19", total: 1_160_000 },
    ]);
    const o = db.prepare(KIS_ORDERS).all("kis_vts", "2026-10-01") as Array<{ name: string; status: string }>;
    expect(o).toHaveLength(1);
    expect(o[0]).toMatchObject({ name: "삼성전자", status: "filled" });
    expect(db.prepare(KIS_NAMES).all(JSON.stringify([1]))).toEqual([{ id: 1, name: "삼성전자" }]);
  });
});

describe("KIS모의투자 글", () => {
  it("보유 칸 — 새 꼴(객체)과 예전 꼴(배열) 둘 다", () => {
    expect(parseHoldings(JSON.stringify([{ id: 1, code: "005930", qty: 3, avg: 9800, price: 10000, value: 30000, pnl: -600 }]))[0]).toMatchObject({ qty: 3, pnl: -600 });
    expect(parseHoldings(JSON.stringify([[1, 3, 9800]]))[0]).toMatchObject({ id: 1, qty: 3, avg: 9800, price: null });
    expect(parseHoldings("깨짐")).toEqual([]);
  });
  it("금액·주문가·상태", () => {
    expect(amountText(1234567.4, "KRW")).toBe("1,234,567원");
    expect(amountText(198.06, "USD")).toBe("$198.06");
    expect(amountText(null, "USD")).toBe("-");
    expect(priceText({ order_type: "market", limit_price: null }, "KRW")).toBe("시장가");
    expect(priceText({ order_type: "limit", limit_price: 9800 }, "KRW")).toBe("9,800원");
    expect(statusText("partial")).toBe("부분 체결");
  });
  it("성과 줄 — 계좌 전체 원화, H 는 손익 금액으로", () => {
    const p = { start: "2026-10-16", start_total: 1e6, last: "2026-10-19", last_total: 1.05e6, pnl_krw: 50_000, return_pct: 5, mdd_pct: -1.5, days: 2, h_pnl_krw: -12_000, h_return_pct: -0.4 };
    const 줄 = perfLines(p);
    expect(줄[0]).toContain("손익 +50,000원 (+5.00%)");
    expect(줄[0]).toContain("최대 낙폭 -1.50%");
    expect(줄[1]).toContain("H 손익 −12,000원 (-0.40%)");
    expect(perfLines(null)[0]).toContain("아직 계좌 총액 기록이 없습니다");
  });
  it("추이 그래프는 시작 총액 1.0 기준 배수, 총액 없는 날은 뺀다", () => {
    expect(kisSeries({ totals: [{ as_of: "a", total: null }, { as_of: "b", total: 200 }, { as_of: "c", total: 210 }] })[0]?.points).toEqual([
      { date: "b", value: 1 },
      { date: "c", value: 1.05 },
    ]);
  });
});
