/** 같은 배당을 두 번 저장하지 않는다 (docs/infra.md 25.829) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { RECENT_SAME_DIVIDEND, dividendBody } from "@/lib/portfolio";

describe("배당 중복 저장", () => {
  it("10분 안·네 칸이 모두 같을 때만 찾는다", () => {
    const d = new DatabaseSync(":memory:");
    const 폴더 = join(process.cwd(), "..", "migrations");
    for (const f of readdirSync(폴더).filter((x) => x.endsWith(".sql")).sort()) d.exec(readFileSync(join(폴더, f), "utf-8"));
    d.exec(`
      INSERT INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at)
      VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', NULL, 'KRW', 'active', 't', 't');
      INSERT INTO dividend_receipts (stock_id, pay_date, gross_amount, tax, net_amount, currency, fx_rate, fx_rate_source, created_at, updated_at)
      VALUES (1, '2026-04-15', 300000, 46200, 253800, 'KRW', 1, 'krw', '2026-10-01T01:00:00.000Z', '2026-10-01T01:00:00.000Z');
    `);
    const 찾기 = (세전: number, 이후: string) => d.prepare(RECENT_SAME_DIVIDEND).get(1, "2026-04-15", 세전, 46200, 이후);
    expect(찾기(300000, "2026-10-01T00:55:00.000Z")).toBeTruthy();
    expect(찾기(300001, "2026-10-01T00:55:00.000Z")).toBeUndefined();
    expect(찾기(300000, "2026-10-01T01:05:00.000Z")).toBeUndefined();
  });

  it("확인했을 때만 표시를 싣고, 경로가 확인 없이 두 번 넣지 않는다", () => {
    expect(dividendBody({ stockId: 1, currency: "KRW", date: "2026-04-15", gross: 1, tax: 0, fx: null })).not.toHaveProperty("confirm_duplicate");
    expect(dividendBody({ stockId: 1, currency: "KRW", date: "2026-04-15", gross: 1, tax: 0, fx: null, confirmed: new Set(["duplicate"]) }).confirm_duplicate).toBe(true);
    const 경로 = readFileSync("app/api/dividends/route.ts", "utf-8");
    expect(경로).toContain("if (!input.confirm_duplicate) {");
    expect(경로.indexOf("RECENT_SAME_DIVIDEND, [")).toBeLessThan(경로.indexOf("INSERT INTO dividend_receipts"));
    expect(readFileSync("components/PortfolioView.tsx", "utf-8")).toContain("return submit(new Set([...confirmed, 물음]));");
  });
});
