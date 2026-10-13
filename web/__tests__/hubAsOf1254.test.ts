import { describe, expect, it } from "vitest";
import { HUB_STALE_DAYS, hubAsOf } from "@/lib/analysis";

// 분석 모아보기 한 줄의 기준 날짜와 묵음 (docs/infra.md 25.1254)
describe("hubAsOf", () => {
  const now = new Date("2026-10-13T03:00:00Z");
  it("오늘 계산이면 날짜만", () => {
    expect(hubAsOf("2026-10-12T23:40:00Z", now)).toEqual({ text: "10-13 기준", stale: false });
  });
  it("주말을 낀 사흘은 묵지 않았다", () => {
    expect(hubAsOf("2026-10-10T00:00:00Z", now)).toEqual({ text: "10-10 기준 · 3일 전", stale: false });
  });
  it(`${HUB_STALE_DAYS}일을 넘으면 묵었다 — 한 번 돌린 참고 분석`, () => {
    expect(hubAsOf("2026-08-01T00:00:00Z", now)).toEqual({ text: "08-01 기준 · 73일 전", stale: true });
  });
  it("못 읽는 시각은 묵은 것으로", () => {
    expect(hubAsOf("", now).stale).toBe(true);
  });
});
