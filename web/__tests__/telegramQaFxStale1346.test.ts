/** 텔레그램 봇: 해외 보유 환율 기준일, "매매 반영 전" 은 그 종목에만 (docs/infra.md 25.1346, 텔레그램 감사 #2·#5) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import type { ResultSet, SqlValue } from "@/lib/db";
import { TRADES_VERSION } from "@/lib/portfolio";
import { STALE_NOTE, answer, type Exec } from "@/lib/telegramQa";

function memExec() {
  const db = new DatabaseSync(":memory:");
  const MIG = join(process.cwd(), "..", "migrations");
  for (const f of readdirSync(MIG).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIG, f), "utf-8"));
  const exec: Exec = async (sql: string, args: SqlValue[] = []) => {
    const st = db.prepare(sql);
    const rows = st.all(...(args as Array<string | number | null>)) as Array<Record<string, unknown>>;
    const columns = st.columns().map((c) => c.name);
    return { columns, rows: rows.map((r) => columns.map((c) => r[c])), affectedRows: 0 } as unknown as ResultSet;
  };
  return { db, exec };
}
const S = "INSERT INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at) VALUES";


describe("텔레그램 봇 (25.1346)", () => {
  it("해외 보유는 환율 기준일이 종가 날과 다르면 함께 적는다", async () => {
    const { db, exec } = memExec();
    db.exec(`${S} (1,'AAPL','NASDAQ','US',NULL,'Apple Inc','USD','active','t','t')`);
    db.exec(`INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,
      unrealized_pnl, unrealized_pnl_krw, unrealized_fx_pnl_krw, price_date, fx_date, updated_at)
      VALUES (1, 10, 'USD', 100, 1300, 1000, 1300000, '2026-09-01', -100, 50000, 160000, '2026-10-13', '2026-10-08', 't')`);
    expect(await answer({ kind: "stock", q: "AAPL" }, exec)).toContain("(2026-10-13) · 환율 기준 2026-10-08");
    db.exec(`UPDATE positions SET fx_date = '2026-10-13'`);
    expect(await answer({ kind: "stock", q: "AAPL" }, exec)).not.toContain("환율 기준");
  });

  it("다른 종목의 매매가 재계산을 기다려도 들고 있지 않은 종목에는 붙이지 않는다", async () => {
    const { db, exec } = memExec();
    db.exec(`${S} (1,'005930','KOSPI','KR','삼성전자',NULL,'KRW','active','t','t'), (2,'000660','KOSPI','KR','SK하이닉스',NULL,'KRW','active','t','t')`);
    db.exec(`INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)
      VALUES (1,'buy','2026-09-01',70000,10,'KRW',1,'none','2026-09-01T00:00:00Z','2026-09-01T00:00:00Z')`);
    const v = String((db.prepare(TRADES_VERSION).get() as { version: string }).version);
    db.exec(`INSERT INTO portfolio_summary (id, as_of_date, totals_json, allocation_json, metrics_json, upcoming_json, warnings_json, trades_version, calc_version, created_at)
      VALUES (1,'2026-10-08','{}','{}','{}','[]','[]','${v}',1,'t')`);
    db.exec(`INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)
      VALUES (1,'buy','2026-10-09',75000,10,'KRW',1,'none','2026-10-09T01:00:00Z','2026-10-09T01:00:00Z')`);
    expect(await answer({ kind: "stock", q: "SK하이닉스" }, exec)).not.toContain(STALE_NOTE); // 예전: 붙었다
    expect(await answer({ kind: "stock", q: "삼성전자" }, exec)).toContain(STALE_NOTE); // 매매가 있는 종목은 그대로
  });
});
