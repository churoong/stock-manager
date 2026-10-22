import { describe, expect, it } from "vitest";
import { WATCHES } from "@/lib/health";

// docs/infra.md 25.1683 — 미국 무응답 알림도 실패 알림과 같게 14:27 UTC(23:27 KST) 예비를 말한다(25.1678)
describe("미국 무응답 알림의 재시도 안내", () => {
  it("23:27 KST 예비 실행을 말한다", () => {
    const us = WATCHES.find((w) => w.job === "daily_us");
    expect(us?.retryNote).toContain("23:27 KST");
    expect(us?.schedule).toContain("23:27 KST");
    expect(us?.dueOffsetMinutes).toBe(0); // 마감은 그대로 — 개장에 리포트가 없는 것은 사실이다
  });
});
