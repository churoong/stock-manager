import { describe, expect, it } from "vitest";
import { gateNowText, vsRToText, type GateItem } from "@/lib/desk";

// 25.2147 — 대조 R 기록이 먼저 멈췄으면 R 대비는 그날까지 잰 값이다(25.2145 `vs_r_to`·관문 `to`). 지금까지로 읽히지 않게 끝을 적는다
describe("R 대비 끝 표시", () => {
  const 관문 = (to?: string | null): GateItem => ({ key: "vs_r", label: "관문 구간 R 대비(%p)", now: 1.5, target: 0, ok: true, to });

  it("관문 R 대비는 대조 기록이 멈춘 날을 적는다", () => {
    expect(gateNowText(관문("2026-10-30"))).toContain("대조 기록이 2026-10-30까지라 그날까지 잼");
    expect(gateNowText(관문(null))).not.toContain("까지라");
    expect(gateNowText(관문())).not.toContain("까지라");
  });

  it("순위표 칸 밑 글은 끝이 있고 R 대비가 있을 때만", () => {
    expect(vsRToText({ vs_r_pp: 0.4, vs_r_to: "2026-10-30" })).toBe("2026-10-30까지");
    expect(vsRToText({ vs_r_pp: 0.4, vs_r_to: null })).toBeNull();
    expect(vsRToText({ vs_r_pp: 0.4 })).toBeNull();
    expect(vsRToText({ vs_r_pp: null, vs_r_to: "2026-10-30" })).toBeNull();
    expect(vsRToText(undefined)).toBeNull();
  });
});
