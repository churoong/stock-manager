import { describe, expect, it } from "vitest";
import { extraColumns } from "@/components/ScreenerForm";
import { defaultFilters } from "@/lib/screener";

/** 걸었거나 정렬한 값의 열을 더한다 (docs/infra.md 25.777, 스크리너 감사) */
describe("걸린 조건의 열", () => {
  const money = (v: number | null) => String(v);
  const base = [{ key: "per", label: "PER", short: "PER", value: () => "" }];
  it("영업이익성장으로 거르면 그 열이 붙는다", () => {
    const f = { ...defaultFilters("KR"), operating_income_growth: { min: 20, max: null } };
    expect(extraColumns(base, f, money, "(1Y)").map((c) => c.key)).toContain("operating_income_growth");
  });
  it("샤프로 정렬하면 기간을 붙인 샤프 열이 붙는다", () => {
    const f = { ...defaultFilters("KR"), sort_by: "sharpe" as const };
    const cols = extraColumns(base, f, money, "(3Y)");
    expect(cols.find((c) => c.key === "sharpe")?.label).toBe("샤프(3Y)");
  });
  it("거래대금으로 거르기만 해도 거래대금 열이 붙는다 (25.779, 교차검증)", () => {
    const f = { ...defaultFilters("KR"), sort_by: "market_cap" as const, avg_turnover_20d: { min: 100, max: null } };
    expect(extraColumns(base, f, money, "").map((c) => c.key)).toContain("turnover");
  });
  it("아무것도 안 걸면 더하지 않고, 이미 있는 열은 겹치지 않는다", () => {
    expect(extraColumns(base, { ...defaultFilters("KR"), sort_by: "per" as const }, money, "")).toHaveLength(0);
    expect(extraColumns(base, null, money, "")).toHaveLength(0);
  });
});
