/** 상태 화면 신선도 — 날짜 색인 내림차순으로 첫 행에서 멈춘다 (docs/infra.md 25.1212) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { FRESHNESS } from "@/lib/health";

function db(): DatabaseSync {
  const d = new DatabaseSync(":memory:");
  const MIG = join(process.cwd(), "..", "migrations");
  for (const f of readdirSync(MIG).filter((x) => x.endsWith(".sql")).sort()) d.exec(readFileSync(join(MIG, f), "utf-8"));
  d.exec(`INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at)
    VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 't', 't'), (2, 'B', 'NYSE', 'US', 'USD', 't', 't');
    INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)
    VALUES (1, '2026-10-08', 1, 'KRW', 't', 't'), (1, '2026-10-07', 1, 'KRW', 't', 't'), (2, '2026-10-09', 1, 'USD', 't', 't');`);
  return d;
}

describe("신선도 질의 (25.1212)", () => {
  it("나라별 시세 날짜가 예전 MAX 와 같고, 행이 없으면 빈 결과(경로가 null 로 읽음)", () => {
    const d = db();
    const 값 = (key: string) => (d.prepare(FRESHNESS.find((f) => f.key === key)!.sql).all() as Array<{ v: string }>)[0]?.v ?? null;
    expect(값("prices_kr")).toBe("2026-10-08");
    expect(값("prices_us")).toBe("2026-10-09");
    expect(값("scores_kr")).toBeNull();
  });

  it("날짜 색인이 있는 표의 나라별 칸은 종목 쪽부터 돌지 않는다", () => {
    const d = db();
    d.exec("ANALYZE"); // 통계가 있으면 예전 MAX 질의는 종목 쪽부터 돌았다
    const 바꾼 = FRESHNESS.filter((f) => f.sql.includes("CROSS JOIN stocks s"));
    expect(바꾼.length).toBe(16);
    for (const f of 바꾼) {
      const 계획 = (d.prepare("EXPLAIN QUERY PLAN " + f.sql).all() as Array<{ detail: string }>).map((r) => r.detail);
      expect(계획[0], f.key).not.toMatch(/^(SCAN|SEARCH) s\b/);
      expect(f.sql, f.key).toMatch(/ORDER BY \w+\.\w+ DESC LIMIT 1$/);
    }
  });
});
