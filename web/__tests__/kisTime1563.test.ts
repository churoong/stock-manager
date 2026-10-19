import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { orderTimeText } from "@/lib/kisVts";

// KIS모의투자 화면 시각 (docs/infra.md 25.1563 — KIS 경로 감사 [참고] 5)
describe("KIS 화면 시각", () => {
  it("주문 시각 여섯 자리를 시:분:초로, 아니면 그대로", () => {
    expect(orderTimeText("093012")).toBe("09:30:12");
    expect(orderTimeText(null)).toBe("");
    expect(orderTimeText("9:30")).toBe("9:30");
  });

  it("방금 조회 시각은 기기 시간대가 아니라 사용자 시각(userTimeOf)으로", () => {
    const src = readFileSync("components/KisPanel.tsx", "utf8");
    expect(src).not.toMatch(/new Date\([^)]*\)\.toLocale/);
    expect(src).toContain("userTimeOf(live.fetched_at)");
    expect(src).toContain("orderTimeText(e.time)");
  });
});
