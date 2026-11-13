/** 같은 배당을 두 번 저장하지 않는다 (docs/infra.md 25.829) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import {
  CONFIRM_REPEAT_WINDOW_MS,
  CONFIRM_UNSAVED_DIVIDEND,
  DIVIDEND_INSERT_UNLESS_RECENT,
  RECENT_SAME_DIVIDEND,
  dividendBody,
  dividendInputSchema,
  insertLowerBound,
} from "@/lib/portfolio";

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
    // 쓰기는 25.1127 부터 상수(`DIVIDEND_INSERT_UNLESS_RECENT`)로 — 확인이 그보다 앞서야 한다
    expect(경로.indexOf("RECENT_SAME_DIVIDEND, [")).toBeGreaterThan(-1);
    expect(경로.indexOf("RECENT_SAME_DIVIDEND, [")).toBeLessThan(경로.indexOf("execute(DIVIDEND_INSERT_UNLESS_RECENT"));
    expect(readFileSync("components/PortfolioView.tsx", "utf-8")).toContain("return submit(new Set([...confirmed, 물음]), confirmSeenAt(j, 물음, seenAt));");
  });
});

// 받은 주식 수(선택)를 함께 보낸다 — 기록한 몫만 시간가중 수익률에 넣는다 (docs/infra.md 25.1261)
describe("배당 주식 수", () => {
  it("적으면 보내고 비우면 null", () => {
    expect(dividendBody({ stockId: 1, currency: "KRW", date: "2026-04-20", gross: 1, tax: 0, fx: null, quantity: 500 }).quantity).toBe(500);
    expect(dividendBody({ stockId: 1, currency: "KRW", date: "2026-04-20", gross: 1, tax: 0, fx: null }).quantity).toBeNull();
  });
});

// 1분 안의 진짜 두 번째 배당을 확인하고 저장하면 들어간다 (docs/infra.md 25.2228) — 매매와 같은 모양
describe("배당 확인 저장의 아래 경계", () => {
  const T0 = "2026-10-10T01:00:00.000Z";
  const 뒤 = (초: number) => new Date(Date.parse(T0) + 초 * 1000).toISOString();
  const 새DB = () => {
    const d = new DatabaseSync(":memory:");
    const 폴더 = join(process.cwd(), "..", "migrations");
    for (const f of readdirSync(폴더).filter((x) => x.endsWith(".sql")).sort()) d.exec(readFileSync(join(폴더, f), "utf-8"));
    d.exec(`INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
      VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')`);
    return d;
  };
  const 넣기 = (d: DatabaseSync, now: string, 이후: string) => {
    const 값 = [1, "2026-10-08", null, null, 300000, 46200, 253800, "KRW", 1, "none", null, now, now];
    return Number(d.prepare(DIVIDEND_INSERT_UNLESS_RECENT).run(...(값 as never[]), 1, "2026-10-08", 300000, 46200, 이후).changes);
  };
  const 건수 = (d: DatabaseSync) => (d.prepare("SELECT COUNT(*) AS n FROM dividend_receipts").get() as { n: number }).n;

  it("confirm_after 를 실은 확인 저장은 넣고 재전송은 막는다", () => {
    const d = 새DB();
    expect(넣기(d, T0, insertLowerBound(Date.parse(T0), 10 * 60_000))).toBe(1);
    // 409 가 본 행 — RECENT_SAME_DIVIDEND 가 그 created_at 을 준다(`seen_at`)
    expect((d.prepare(RECENT_SAME_DIVIDEND).get(1, "2026-10-08", 300000, 46200, "2026-10-10T00:51:00.000Z") as { created_at: string }).created_at).toBe(T0);
    expect(넣기(d, 뒤(20), insertLowerBound(Date.parse(뒤(20)), CONFIRM_REPEAT_WINDOW_MS, T0))).toBe(1);
    expect(넣기(d, 뒤(25), insertLowerBound(Date.parse(뒤(25)), CONFIRM_REPEAT_WINDOW_MS, T0))).toBe(0);
    expect(건수(d)).toBe(2);
  });

  it("confirm_after 없는 확인은 0행이고 경로는 저장하지 않았다고 말한다", () => {
    const d = 새DB();
    expect(넣기(d, T0, insertLowerBound(Date.parse(T0), 10 * 60_000))).toBe(1);
    expect(넣기(d, 뒤(20), insertLowerBound(Date.parse(뒤(20)), CONFIRM_REPEAT_WINDOW_MS))).toBe(0);
    expect(건수(d)).toBe(1);
    expect(CONFIRM_UNSAVED_DIVIDEND).toContain("저장하지 않았습니다");
    const 경로 = readFileSync("app/api/dividends/route.ts", "utf-8");
    expect(경로).toContain("seen_at: 같은.created_at");
    expect(경로).toContain("insertLowerBound(Date.now(), CONFIRM_REPEAT_WINDOW_MS, input.confirm_after)");
    expect(경로).toContain(": CONFIRM_UNSAVED_DIVIDEND;");
    expect(dividendInputSchema.safeParse({ stock_id: 1, pay_date: "2026-10-08", gross_amount: 1, confirm_duplicate: true, confirm_after: T0 }).success).toBe(true);
    expect(dividendInputSchema.safeParse({ stock_id: 1, pay_date: "2026-10-08", gross_amount: 1, confirm_after: "x" }).success).toBe(false);
  });

  it("폼 본문이 seenAt 을 confirm_after 로 싣는다(중복을 확인했을 때만)", () => {
    expect(dividendBody({ stockId: 1, currency: "KRW", date: "2026-10-08", gross: 1, tax: 0, fx: null, confirmed: new Set(["duplicate"]), seenAt: T0 }).confirm_after).toBe(T0);
    expect(dividendBody({ stockId: 1, currency: "KRW", date: "2026-10-08", gross: 1, tax: 0, fx: null, seenAt: T0 })).not.toHaveProperty("confirm_after");
  });
});
