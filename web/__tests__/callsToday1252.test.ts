import { describe, expect, it } from "vitest";
import { callsTodayText } from "@/lib/health";

// "오늘 N회" 는 기록된 한국 날짜가 오늘일 때만 (docs/infra.md 25.1252)
describe("callsTodayText", () => {
  const now = new Date("2026-10-13T03:00:00Z"); // 한국 10-13 12:00
  it("오늘 기록이면 그 횟수", () => {
    expect(callsTodayText({ calls_today: 12, day: "2026-10-13" }, now)).toBe("오늘 12회");
  });
  it("어제 기록이면 오늘 0회와 그날 횟수", () => {
    expect(callsTodayText({ calls_today: 12, day: "2026-10-12" }, now)).toBe("오늘 0회 (10-12 12회)");
  });
  it("한국 자정 넘김은 한국 날짜로 본다", () => {
    expect(callsTodayText({ calls_today: 3, day: "2026-10-14" }, new Date("2026-10-13T15:30:00Z"))).toBe("오늘 3회");
  });
});
