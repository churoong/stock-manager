import { describe, expect, it } from "vitest";
import { indexShockText } from "@/lib/desk";

/** 위험판 "지수 충격" 칸 (docs/desk.md 6장, docs/infra.md 25.2281) */
describe("indexShockText", () => {
  it("예상 손익이라 부호를 그대로, 소수 한 자리", () => {
    expect(indexShockText({ index_shock_pct: -3.449 })).toBe("−3.4%");
    expect(indexShockText({ index_shock_pct: 0.5 })).toBe("+0.5%");
    expect(indexShockText({ index_shock_pct: 0 })).toBe("0.0%");
    expect(indexShockText({ index_shock_pct: -0.04 })).toBe("0.0%"); // 반올림해 0.0 이면 부호 없이 (25.2282)
    expect(indexShockText({ index_shock_pct: 0.049 })).toBe("0.0%");
    expect(indexShockText({ index_shock_pct: -0.05 })).toBe("−0.1%");
  });
  it("값이 없으면 - (ETF 전술 · 베타를 못 읽은 날 · 이 칸이 없던 날)", () => {
    expect(indexShockText({ index_shock_pct: null })).toBe("-");
    expect(indexShockText({})).toBe("-");
    expect(indexShockText({ index_shock_pct: Number.NaN })).toBe("-");
  });
  it("가정과 못 잰 수를 숨기지 않는다", () => {
    expect(
      indexShockText({ index_shock_pct: -5.5, index_shock_assumed: 2 }),
    ).toBe("−5.5% (2종목 베타 1 가정)");
    expect(
      indexShockText({ index_shock_pct: -2, index_shock_skipped: 1 }),
    ).toBe("−2.0% (1종목 못 잼)");
    expect(
      indexShockText({
        index_shock_pct: -2,
        index_shock_assumed: 3,
        index_shock_skipped: 1,
      }),
    ).toBe("−2.0% (3종목 베타 1 가정 · 1종목 못 잼)");
    expect(
      indexShockText({ index_shock_pct: null, index_shock_skipped: 1 }),
    ).toBe("- (1종목 못 잼)");
    expect(
      indexShockText({ index_shock_pct: -1, index_shock_assumed: 0 }),
    ).toBe("−1.0%");
  });
});
