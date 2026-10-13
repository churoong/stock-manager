// 세후 적립 카드가 쓴 세율과 제도 금액을 저장값에서 보인다 (docs/infra.md 25.1301, 세후 적립 감사 #1·#5)
import { describe, expect, it } from "vitest";
import { taxSimLines } from "@/lib/taxSim";

const 행 = [{ key: "general_kr_equity", label: "일반 · 국내 주식 ETF", years: 10, contributed: 6e7, after_tax: 7e7, tax: 1e6, missing: [] }];

describe("세후 적립 카드 머리", () => {
  it("제도 금액·쓴 세율은 저장값에서", () => {
    const v = taxSimLines({
      assumption: { monthly_krw: 500000, price_return_pct: 5, dist_yield_pct: 2, us_gain_deduction_krw: 3_000_000,
        fin_income_comprehensive_krw: 25_000_000, rates: { kr_dividend_pct: 15.4, pension_income_pct: null } },
      rows: 행,
    })!;
    expect(v.head).toContain("해마다 300만원 공제");
    expect(v.head).toContain("한 해 2,500만원 초과");
    expect(v.head).toContain("쓴 세율: 국내 배당소득세 15.4% · 연금소득세 미입력");
  });
  it("예전 저장값(칸 없음)도 숫자를 지어내지 않고 읽힌다", () => {
    const v = taxSimLines({ assumption: { monthly_krw: 500000, price_return_pct: 5, dist_yield_pct: 2 }, rows: 행 })!;
    expect(v.head).not.toContain("250만원");
    expect(v.head).not.toContain("쓴 세율");
  });
});
