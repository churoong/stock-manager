/** 계좌별 세후 적립 시뮬레이션 문장 (docs/etf.md 11.7, docs/infra.md 25.1003) — 배치 값을 글로만 바꾼다 */
import { describe, expect, it } from "vitest";
import { taxSimLines, type TaxSim } from "@/lib/taxSim";

const SIM: TaxSim = {
  assumption: { monthly_krw: 500_000, price_return_pct: 5, dist_yield_pct: 2 },
  rows: [
    { key: "general_kr_equity", label: "일반 · 국내 주식 ETF", years: 10, contributed: 60_000_000, after_tax: 85_600_000, tax: 1_180_000, missing: [] },
    { key: "pension_kr_listed_foreign", label: "연금저축 · 국내 상장 해외 ETF", years: 10, contributed: 60_000_000, after_tax: 90_220_000, tax: -3_130_000, missing: [] },
    { key: "general_us_listed", label: "일반 · 미국 상장 ETF", years: 10, contributed: 60_000_000, after_tax: null, tax: null, missing: ["us_capital_gains_pct"] },
  ],
};

describe("세후 시뮬레이션", () => {
  it("가장 큰 줄을 표시하고 세액공제가 더 크면 그렇게 말한다", () => {
    const v = taxSimLines(SIM)!;
    expect(v.head).toContain("가정했을 때");
    expect(v.groups).toHaveLength(1);
    expect(v.groups[0].lines[0]).toBe("일반 · 국내 주식 ETF: 세후 8,560만원 (넣은 돈 6,000만원, 세금 118만원)");
    expect(v.groups[0].lines[1]).toBe("연금저축 · 국내 상장 해외 ETF: 세후 9,022만원 (넣은 돈 6,000만원, 세액공제가 세금보다 313만원 많음) ← 가장 큼");
  });

  it("세율이 비면 숫자 없이 무엇을 넣을지 적는다", () => {
    expect(taxSimLines(SIM)!.groups[0].lines[2]).toBe("일반 · 미국 상장 ETF: 설정 세율 미입력 (해외 양도소득세)");
  });

  it("없으면 null", () => {
    expect(taxSimLines(null)).toBeNull();
  });
});
