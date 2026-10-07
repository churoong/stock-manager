import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * **판 적이 있는 매수를 지우면 외래키에 걸렸다** (docs/infra.md 25.225).
 *
 * `DELETE /api/trades/[id]` 는 파생 표 `trade_lots` 만 먼저 비웠다. 복기 표 `trade_reviews.buy_trade_id` 도
 * `trades(id)` 를 가리킨다(0024). 외래키가 켜진 DB 에서는 복기가 있는 매수를 지울 수 없다 — 그리고 경로는 500 만 준다.
 * 사용자는 잘못 넣은 매수를 고칠 길이 막힌다("고치기는 지우고 다시 넣는다", 25.150).
 */

let db: DatabaseSync;

beforeEach(() => {
  vi.resetModules();
  db = new DatabaseSync(":memory:");
  for (const f of readdirSync(join(process.cwd(), "..", "migrations")).filter((x) => x.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(process.cwd(), "..", "migrations", f), "utf-8"));
  }
  db.exec("PRAGMA foreign_keys = ON");
  db.exec(`INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)
      VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't');
    INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)
      VALUES (1, 1, 'buy', '2026-09-01', 100, 10, 'KRW', 1, 'none', 't', 't'),
             (2, 1, 'sell', '2026-09-10', 110, 10, 'KRW', 1, 'none', 't', 't');
    INSERT INTO trade_reviews (buy_trade_id, stock_id, buy_date, last_sell_date, currency, quantity_sold, quantity_bought,
      partial, cost, proceeds, return_pct, holding_days, realized_pnl_krw, price_pnl_krw, fx_pnl_krw, has_snapshot,
      outcome, verdict_text, calc_version, created_at)
      VALUES (1, 1, '2026-09-01', '2026-09-10', 'KRW', 10, 10, 0, 1000, 1100, 0.1, 9, 100, 100, 0, 0, 'gain', 'x', 1, 't');`);

  vi.doMock("@/lib/db", async (orig) => {
    const 진짜 = await (orig as () => Promise<Record<string, unknown>>)();
    const 돌리기 = async (sql: string, args: unknown[] = []) => {
      const stmt = db.prepare(sql);
      if (!/^\s*(SELECT|WITH)/i.test(sql)) {
        const r = stmt.run(...(args as never[]));
        return { columns: [], rows: [], affectedRows: Number(r.changes) };
      }
      const rows = stmt.all(...(args as never[])) as Array<Record<string, unknown>>;
      const columns = rows[0] ? Object.keys(rows[0]) : stmt.columns().map((c) => c.name);
      return { columns, rows: rows.map((r) => columns.map((c) => r[c])), affectedRows: 0 };
    };
    return { ...진짜, execute: 돌리기 };
  });
  vi.doMock("@/lib/portfolio", async (orig) => ({
    ...(await (orig as () => Promise<Record<string, unknown>>)()),
    requestRecalc: async () => "skipped",
  }));
});

afterEach(() => {
  vi.doUnmock("@/lib/db");
  vi.doUnmock("@/lib/portfolio");
});

async function 지우기(id: number) {
  const { DELETE } = await import("@/app/api/trades/[id]/route");
  const res = await DELETE(new Request(`https://x/api/trades/${id}`, { method: "DELETE" }), {
    params: Promise.resolve({ id: String(id) }),
  } as never);
  return res.status;
}

describe("외래키가 켜진 DB 에서 지우기", () => {
  it("미끼 — 외래키가 실제로 켜져 있다", () => {
    expect(() => db.exec("DELETE FROM trades WHERE id = 1")).toThrow(/FOREIGN KEY/);
  });

  it("복기가 있는 매수도 지워진다 — 복기는 파생이라 다음 재계산이 다시 만든다", async () => {
    // 매도부터 지운다(판 기록이 남으면 25.150 이 경고할 뿐 막지는 않는다)
    expect(await 지우기(2)).toBe(200);
    expect(await 지우기(1)).toBe(200);
    expect((db.prepare("SELECT COUNT(*) AS n FROM trades").get() as { n: number }).n).toBe(0);
  });
});
