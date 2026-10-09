/** 종합 점수 분해 (docs/analysis.md 26장, docs/infra.md 25.1051) */
import { describe, expect, it } from "vitest";
import { barWidth } from "@/components/ScoreWaterfall";

describe("점수 분해 막대", () => {
  it("가장 큰 몫이 100%, 음수도 크기로, 0 으로 나누지 않는다", () => {
    expect(barWidth(20, 20)).toBe(100);
    expect(barWidth(-5, 20)).toBe(25);
    expect(barWidth(3, 0)).toBe(0);
  });
});
