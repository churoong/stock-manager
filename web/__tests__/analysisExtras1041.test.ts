/** 네 개의 눈 (docs/analysis.md 19장, docs/infra.md 25.1041) — 수직선 위 자리 */
import { describe, expect, it } from "vitest";
import { MODEL_LABEL, agreementPositions } from "@/lib/analysis";

describe("네 개의 눈", () => {
  it("0% 를 늘 선 안에 두고 값의 비례로 놓는다", () => {
    const p = agreementPositions({ views: { capm: 0.08, scenario: -0.1, consensus: 0.3 }, up: 2, n: 3, spread: 0.4 });
    expect(p.zero).toBeCloseTo(25);
    expect(p.marks.find((m) => m.model === "consensus")!.pos).toBeCloseTo(100);
    expect(p.marks.find((m) => m.model === "scenario")!.pos).toBeCloseTo(0);
    // 모두 오름이어도 0% 가 왼쪽 끝에 있다
    expect(agreementPositions({ views: { capm: 0.1, analog: 0.2 }, up: 2, n: 2, spread: 0.1 }).zero).toBe(0);
  });

  it("네 눈의 이름을 모두 안다", () => {
    for (const k of ["capm", "analog", "scenario", "consensus"]) expect(MODEL_LABEL[k]).toBeTruthy();
  });
});
