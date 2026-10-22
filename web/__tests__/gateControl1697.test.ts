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

describe("관문 R 대비 — 노출 맞춘 참고 (25.1702)", () => {
  it("값이 있으면 괄호로, 없으면 예전 글", () => {
    const 칸 = { key: "vs_r", label: "", now: 3.13, target: 0, ok: true };
    expect(gateNowText(칸)).toBe("+3.13%p / 0 초과");
    expect(
      gateNowText({ ...칸, exposure_matched: { vs_r: -0.2, r_pct: 1, h_exposure_pct: 70.4, r_exposure_pct: 99.6, skipped_days: 0 } }),
    ).toBe("+3.13%p / 0 초과 (참고: 노출 맞춘 R 대비 -0.20%p — 평균 투자 비중 H 70% · R 100%)");
    expect(gateNowText({ ...칸, exposure_matched: null })).toBe("+3.13%p / 0 초과");
  });
});

describe("관문 R 대비 — 원가·묵은 평가 (25.1709)", () => {
  it("문턱을 넘었을 때만 까닭을 붙인다", () => {
    const 칸 = { key: "vs_r", label: "", now: 1, target: 0, ok: false };
    expect(gateNowText({ ...칸, valuation_gap: { h_pct: 1.2, r_pct: 7.04, limit: 5, over: true } })).toBe(
      "+1.00%p / 0 초과 · 원가·묵은 평가 H 1.2% · R 7.0% — 5% 넘어 믿기 어려움",
    );
    expect(gateNowText({ ...칸, valuation_gap: { h_pct: 1, r_pct: 2, limit: 5, over: false } })).toBe("+1.00%p / 0 초과");
  });
});
