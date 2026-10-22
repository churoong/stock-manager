import { describe, expect, it } from "vitest";
import { actionDayToday, actionSuspect, dropOnActionSuspect } from "@/lib/intraday";

// docs/infra.md 25.1725 — 권리락 첫날 가드가 KIS 시세에만 걸려, 야후로 판정하면 거짓 손절이 나갔다(장중 감사 [필수] 1)
describe("기업행위 첫날 표시 (배치 예탁원 일정)", () => {
  it("야후 시세면 기준가 가드가 안 걸리던 꼴 — 배치 표시가 그날만 잡는다", () => {
    // 25.1551 과 같은 숫자: DB 전일 10,000 · 기준가 7,140 · 현재 7,100. 야후 전일 종가가 조정 안 됐으면(10,000) 의심 못 함
    expect(actionSuspect("KR", 7_100, 10_000, 10_000, false)).toBe(false);
    expect(actionDayToday(["holding", "action_day:2026-10-23"], "2026-10-23")).toBe(true);
    // 묵은 목록(다른 날 표시)은 그날 알림을 빼지 않는다
    expect(actionDayToday(["holding", "action_day:2026-10-23"], "2026-10-24")).toBe(false);
    expect(actionDayToday(["holding"], "2026-10-23")).toBe(false);
    expect(dropOnActionSuspect("KR").has("stop")).toBe(true);
  });
});
