/**
 * 관문 가상 대조 칸 — 산 새 종목의 무작위 대조 분위 묶음 하한 (docs/desk.md 6장, docs/infra.md 25.1697).
 */

import { describe, expect, it } from "vitest";
import { gateNowText } from "@/lib/desk";

describe("관문 가상 대조 칸", () => {
  it("결정 단위 선정과 같은 글 — 하한·평균·묶음 수, 하한 전엔 평균만", () => {
    expect(gateNowText({ key: "control", label: "", now: 53.21, target: 50, ok: true, n: 6, mean: 60.4 })).toBe(
      "하한 53.2분위(평균 60.4, 28일 묶음 6) / 50 초과",
    );
    expect(gateNowText({ key: "control", label: "", now: null, target: 50, ok: false, n: 3, mean: 57 })).toBe(
      "하한 모름(평균 57.0, 28일 묶음 3) / 50 초과",
    );
    expect(gateNowText({ key: "control", label: "", now: null, target: 50, ok: false, n: 0 })).toBe("모름");
  });
});
