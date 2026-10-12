// 종목 상세 −0 표시·ETF 구성 몫 끝값·복기 수익률과 표지 엇갈림 (docs/infra.md 25.1217)
import { describe, expect, it } from "vitest";
import { formatNum, formatPct } from "@/lib/stockDetail";
import { coveredText, pct, pctClass, reviewPctDigits } from "@/lib/portfolio";

describe("종목 상세 성과표는 −0 을 적지 않는다", () => {
  it("formatPct", () => {
    expect(formatPct(-0.0004)).toBe("0.0%");
    expect(formatPct(-0.0012)).toBe("-0.1%");
    expect(formatPct(0.123)).toBe("12.3%");
  });
  it("formatNum", () => {
    expect(formatNum(-0.004)).toBe("0.00");
    expect(formatNum(-0.006)).toBe("-0.01");
    expect(formatNum(1.234)).toBe("1.23");
  });
});

describe("ETF 구성을 아는 몫은 반올림으로 끝값이 되지 않는다", () => {
  it("99.6 은 100% 가 아니다, 0.4 는 0% 가 아니다", () => {
    expect(coveredText(99.6)).toBe("99% 넘게");
    expect(coveredText(0.4)).toBe("1% 미만");
    expect(coveredText(100)).toBe("100%");
    expect(coveredText(0)).toBe("0%");
    expect(coveredText(57.2)).toBe("57%");
  });
});

describe("복기 수익률은 손실 표지 옆에서 0.0% 가 되지 않는다", () => {
  it("작은 손실은 두 자리", () => {
    const r = -0.0004;
    const d = reviewPctDigits(r);
    expect(pct(r, d)).toBe("-0.04%");
    expect(pctClass(r, d)).toBe("text-blue-600");
  });
  it("보통 값은 한 자리 그대로", () => {
    expect(reviewPctDigits(0.123)).toBe(1);
    expect(reviewPctDigits(0)).toBe(1);
    expect(reviewPctDigits(null)).toBe(1);
  });
});
