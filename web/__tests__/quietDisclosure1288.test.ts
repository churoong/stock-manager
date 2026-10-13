import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { DISCLOSURE_ALERTS_BETWEEN, DISCLOSURE_ALERTS_FOR } from "@/lib/intraday";

// 조용시간에 "보내지 않음" 으로 둔 공시는 조용시간 밖에서 알린 것으로 보지 않는다 (docs/infra.md 25.1288, 장중 감사 재현)
describe("조용시간 공시", () => {
  const db = new DatabaseSync(":memory:");
  const dir = join(process.cwd(), "..", "migrations");
  for (const f of readdirSync(dir).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(dir, f), "utf-8"));
  db.exec(`INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at) VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 't', 't')`);
  db.exec(`INSERT INTO alerts (stock_id, market, trade_date, trigger_type, message, data, created_at, sent_at)
           VALUES (1, 'KR', '2026-10-13', 'disclosure', 'm', '{"rcept_nos":["20261013000001"]}', 't', 'quiet-skipped')`);
  const 수 = (sql: string, ...args: Array<string | number>) => (db.prepare(sql).all(...args) as unknown[]).length;

  it("조용시간 밖(1)이면 보내지 않은 행을 빼고 DART 를 다시 묻게 한다", () => {
    expect(수(DISCLOSURE_ALERTS_FOR, "[1]", "2026-10-13", 1)).toBe(0);
    expect(수(DISCLOSURE_ALERTS_BETWEEN, "[1]", "2026-10-13", "2026-10-14", 1)).toBe(0);
  });

  it("조용시간 안(0)이면 예전처럼 센다", () => {
    expect(수(DISCLOSURE_ALERTS_FOR, "[1]", "2026-10-13", 0)).toBe(1);
  });

  it("경로가 조용시간 여부를 넘긴다", () => {
    const 글 = readFileSync("app/api/cron/intraday/route.ts", "utf8");
    expect(글).toContain("[JSON.stringify(보유ids), day, quiet ? 0 : 1]");
  });
});
