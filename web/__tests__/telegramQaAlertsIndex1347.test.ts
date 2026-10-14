/** `/알림` 이 (시장, 거래일) 색인을 탄다 (docs/infra.md 25.1347, 텔레그램 감사 #4) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";

describe("알림 색인", () => {
  it("시장마다 마지막 거래일을 색인으로 찾는다", () => {
    const db = new DatabaseSync(":memory:");
    const MIG = join(process.cwd(), "..", "migrations");
    for (const f of readdirSync(MIG).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIG, f), "utf-8"));
    const plan = db.prepare("EXPLAIN QUERY PLAN SELECT market, MAX(trade_date) AS d FROM alerts GROUP BY market").all() as Array<{ detail: string }>;
    expect(plan.map((p) => p.detail).join(" ")).toContain("idx_alerts_market_date");
  });
});
