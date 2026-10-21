/** 관문 KIS 칸 — 경로 포함 실현율을 참고로 함께 (docs/infra.md 25.1624) */
import { describe, expect, it } from "vitest";
import { gateNowText } from "../lib/desk";

describe("경로 포함 실현율", () => {
  it("있으면 참고로 붙이고 없으면 예전 글", () => {
    const 칸 = { key: "kis", label: "", now: 90, target: 90, ok: true, n: 30 };
    expect(gateNowText({ ...칸, path_pct: 77.14, path_n: 35 })).toBe(
      "90.0% (표본 30) / 90% 이상 · 경로 포함 77.1%(표본 35, 참고)",
    );
    expect(gateNowText(칸)).toBe("90.0% (표본 30) / 90% 이상");
  });
});

describe("실현율 모름일 때도 경로 포함 값 (25.1627)", () => {
  it("가상 체결이 모두 경로 실패면 모름과 함께", () => {
    expect(
      gateNowText({
        key: "kis",
        label: "",
        now: null,
        target: 90,
        ok: false,
        n: 0,
        path_pct: 0,
        path_n: 3,
      }),
    ).toBe("모름 · 경로 포함 0.0%(표본 3, 참고)");
  });
});
