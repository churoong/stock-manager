import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeEach, describe, expect, it } from "vitest";
import { BUY_COST_UNTIL, DIVIDEND_INSERT_UNLESS_RECENT, zeroWithholdingNote } from "@/lib/portfolio";

/** 배당 감사 (docs/infra.md 25.1127) — 동시 두 번 저장, 세금 칸 비움 → 0, 지급일 당일 매수를 원금에 넣음 */
const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  db.exec(`INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
    VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')`);
});

function 넣기(now: string, 이후: string, gross = 300000): number {
  const 값 = [1, "2026-10-08", null, null, gross, 46200, gross - 46200, "KRW", 1, "none", null, now, now];
  return Number(db.prepare(DIVIDEND_INSERT_UNLESS_RECENT).run(...(값 as never[]), 1, "2026-10-08", gross, 46200, 이후).changes);
}

describe("같은 배당이 방금 있으면 한 문장 안에서 넣지 않는다", () => {
  it("두 번째 같은 요청은 0행, 금액이 다르면 넣는다", () => {
    expect(넣기("2026-10-10T01:00:00.000Z", "2026-10-10T00:50:00.000Z")).toBe(1);
    expect(넣기("2026-10-10T01:00:01.000Z", "2026-10-10T00:50:01.000Z")).toBe(0);
    expect(넣기("2026-10-10T01:00:02.000Z", "2026-10-10T00:50:02.000Z", 310000)).toBe(1);
    expect((db.prepare("SELECT COUNT(*) AS n FROM dividend_receipts").get() as { n: number }).n).toBe(2);
  });

  it("경로가 확인 없는 저장에 한 문장 쓰기를 쓰고 0행이면 409", () => {
    const 원문 = readFileSync(join(process.cwd(), "app", "api", "dividends", "route.ts"), "utf-8");
    expect(원문).toContain("execute(DIVIDEND_INSERT_UNLESS_RECENT");
    expect(원문).toMatch(/affectedRows === 0[\s\S]{0,400}status: 409/);
  });
});

describe("세율이 있는데 세금 0 이면 말한다", () => {
  it("세율·세전이 있을 때만", () => {
    expect(zeroWithholdingNote(0, 300000, 15.4, "KRW")).toContain("약 46200 KRW");
    expect(zeroWithholdingNote(46200, 300000, 15.4, "KRW")).toBeNull();
    expect(zeroWithholdingNote(0, 300000, null, "KRW")).toBeNull();
    expect(zeroWithholdingNote(0, 300000, 0, "KRW")).toBeNull();
    expect(zeroWithholdingNote(0, 10, 15, "USD")).toContain("약 1.5 USD");
    const 원문 = readFileSync(join(process.cwd(), "app", "api", "dividends", "route.ts"), "utf-8");
    expect(원문).toContain("zeroWithholdingNote(input.tax, input.gross_amount");
  });
});

describe("배당 크기 확인의 원금은 지급일 전 매수만", () => {
  it("지급일 당일 매수는 빼고 본다 (배치 25.932 와 같게)", () => {
    db.exec(`INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)
      VALUES (1, 'buy', '2026-10-08', 250, 10, 'KRW', 1, 'none', 't', 't'), (1, 'buy', '2026-10-01', 100, 10, 'KRW', 1, 'none', 't', 't')`);
    expect((db.prepare(BUY_COST_UNTIL).get(1, "2026-10-08") as { cost: number }).cost).toBe(1000);
  });
});
