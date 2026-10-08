/** 예상 범위 부채꼴 (docs/analysis.md 10.4, docs/infra.md 25.1044) */
import { describe, expect, it } from "vitest";
import { fanX } from "@/components/ForecastFan";

describe("부채꼴 가로 눈금", () => {
  it("0 에서 시작해 1년에 끝나고 기간 순서를 지킨다(제곱근)", () => {
    expect(fanX(0)).toBe(0);
    expect(fanX(12)).toBe(1);
    expect(fanX(3)).toBeCloseTo(0.5);
    expect(fanX(1) < fanX(3) && fanX(3) < fanX(6) && fanX(6) < fanX(12)).toBe(true);
  });
});
