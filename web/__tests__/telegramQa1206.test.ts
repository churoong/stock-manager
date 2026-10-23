/** 텔레그램 질의응답 감사 넷 (docs/infra.md 25.1206): 대소문자·매매 반영 전 표시·플래그 판정일·같은 티커 두 행 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import type { ResultSet, SqlValue } from "@/lib/db";
import { TRADES_VERSION } from "@/lib/portfolio";
import { STALE_NOTE, answer, parseQuery, type Exec } from "@/lib/telegramQa";

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

describe("텔레그램 질의응답 (25.1206)", () => {
  it("한글 이름의 영문자는 대소문자를 가리지 않는다", async () => {
    const { db, exec } = memExec();
    db.exec(`${S} (1,'000660','KOSPI','KR','SK하이닉스','SK hynix','KRW','active','t','t'),
      (2,'066570','KOSPI','KR','LG전자',NULL,'KRW','active','t','t')`);
    expect((await answer({ kind: "stock", q: "sk하이닉스" }, exec)).split("\n")[0]).toBe("SK하이닉스 (000660)");
    expect((await answer({ kind: "stock", q: "lg전자" }, exec)).split("\n")[0]).toBe("LG전자 (066570)");
  });

  it("매매를 넣고 보유 재계산 전이면 그렇다고 붙인다", async () => {
    const { db, exec } = memExec();
    db.exec(`${S} (1,'005930','KOSPI','KR','삼성전자',NULL,'KRW','active','t','t')`);
    db.exec(`INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,
      unrealized_pnl_krw, weight_pct, price_date, updated_at) VALUES (1, 10, 'KRW', 70000, 1, 700000, 700000, '2026-09-01', 50000, 100, '2026-10-08', 't')`);
    db.exec(`INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)
      VALUES (1,'buy','2026-09-01',70000,10,'KRW',1,'none','2026-09-01T00:00:00Z','2026-09-01T00:00:00Z')`);
    const v = String((db.prepare(TRADES_VERSION).get() as { version: string }).version);
    db.exec(`INSERT INTO portfolio_summary (id, as_of_date, totals_json, allocation_json, metrics_json, upcoming_json, warnings_json, trades_version, calc_version, created_at)
      VALUES (1,'2026-10-08','{}','{}','{}','[]','[]','${v}',1,'t')`);
    expect(await answer({ kind: "holdings" }, exec)).not.toContain(STALE_NOTE); // 맞을 때는 조용하다
    db.exec(`INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)
      VALUES (1,'sell','2026-10-09',75000,10,'KRW',1,'none','2026-10-09T01:00:00Z','2026-10-09T01:00:00Z')`);
    expect(await answer({ kind: "holdings" }, exec)).toContain(STALE_NOTE);
    expect(await answer({ kind: "stock", q: "삼성전자" }, exec)).toContain(STALE_NOTE);
  });

  it("매도 플래그에 판정일을 붙인다", async () => {
    const { db, exec } = memExec();
    db.exec(`${S} (1,'005930','KOSPI','KR','삼성전자',NULL,'KRW','active','t','t')`);
    db.exec(`INSERT INTO sell_flags (stock_id, as_of_date, level, reason_code, rationale_text, rationale_data, first_seen_date, created_at)
      VALUES (1,'2026-09-15','red','손절','손절가 이탈','{}','2026-09-15','t')`);
    expect(await answer({ kind: "stock", q: "삼성전자" }, exec)).toContain("매도 플래그(🔴, 2026-09-15): 손절가 이탈");
  });

  it("같은 티커·이름의 두 행이면 하나로 묶고 보유가 있는 행을 고른다", async () => {
    const { db, exec } = memExec();
    db.exec(`${S} (1,'WMT','NYSE','US',NULL,'Walmart Inc.','USD','excluded','t','t'), (2,'WMT','NASDAQ','US',NULL,'Walmart Inc.','USD','active','t','t')`);
    db.exec(`INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)
      VALUES (1,'buy','2025-06-01',90,5,'USD',1380,'auto','t','t')`);
    db.exec(`INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date, updated_at)
      VALUES (1, 5, 'USD', 90, 1380, 450, 621000, '2025-06-01', 't')`);
    for (const q of ["WMT", "Walmart Inc.", "/종목 WMT"]) {
      const out = await answer(parseQuery(q)!, exec);
      expect(out).not.toContain("여럿이 맞습니다");
      expect(out).toContain("보유 5주");
    }
  });
});
