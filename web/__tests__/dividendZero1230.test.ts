// 세전 0 배당은 받지 않는다 (docs/infra.md 25.1230)
import { describe, expect, it } from "vitest";
import { dividendInputSchema } from "@/lib/portfolio";

describe("배당 세전 금액", () => {
  it("0 은 거절", () => {
    const r = dividendInputSchema.safeParse({ stock_id: 1, pay_date: "2026-09-01", gross_amount: 0, tax: 0 });
    expect(r.success).toBe(false);
  });
  it("양수는 받는다", () => {
    expect(dividendInputSchema.safeParse({ stock_id: 1, pay_date: "2026-09-01", gross_amount: 1000, tax: 154 }).success).toBe(true);
  });
});
