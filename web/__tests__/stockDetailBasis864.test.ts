/** 종목 상세의 밴드 기준 표시는 밴드 기준일까지 접수된 보고서로 고른다 (docs/infra.md 25.861·25.864) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { expect, it } from "vitest";
import { VALUATION } from "@/lib/stockDetail";

it("밴드 기준일 뒤에 처음 낸 연결은 그 밴드의 기준을 연결로 바꾸지 않는다", () => {
  const db = new DatabaseSync(":memory:");
  const dir = join(process.cwd(), "..", "migrations");
  for (const f of readdirSync(dir).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(dir, f), "utf-8"));
  db.exec(`INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)
    VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')`);
  const 재무 = db.prepare(`INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date,
    receipt_no, currency, unit, total_equity, source, fetched_at) VALUES (1, ?, '11011', 'A', ?, ?, ?, 'KRW', '원', 100, 't', 't')`);
  재무.run(2024, 0, "2025-03-10", "r1");
  재무.run(2025, 1, "2026-03-10", "r2"); // 밴드 기준일(2026-01-15) 뒤 첫 연결
  const 열 = (db.prepare("PRAGMA table_info(valuation_bands)").all() as Array<{ name: string; notnull: number; dflt_value: unknown; pk: number }>)
    .filter((c) => c.notnull && c.dflt_value === null && !c.pk);
  const 값 = 열.map((c) => (c.name === "stock_id" ? 1 : c.name === "as_of_date" ? "2026-01-15" : /calc_version|sample/.test(c.name) ? 1 : "x"));
  db.prepare(`INSERT INTO valuation_bands (${열.map((c) => c.name).join(", ")}) VALUES (${열.map(() => "?").join(", ")})`).run(...(값 as never[]));
  const 행 = db.prepare(VALUATION).get(1) as { equity_consolidated: number };
  expect(행.equity_consolidated).toBe(0);
});
