// 25.2022 (결함 루프) — 위험판 손절 가까움: 스윙·단타는 기간 손절선으로 재지 않고 그 까닭 글을 보인다
import { describe, expect, it } from "vitest";
import { nearStopText } from "@/lib/desk";

describe("nearStopText", () => {
  it("가까운 보유·까닭 글·없음", () => {
    expect(nearStopText({ near_stop: [{ name: "가", unrealized_pct: -5.5, stop_pct: -7 }] })).toEqual({ text: "가 -5.5%(선 -7%)", muted: false });
    expect(nearStopText({ near_stop: [], near_stop_note: "해당 없음 — 스윙은 진입 시가 대비 종가 -3%(다음 장 시가 매도)" })).toEqual({
      text: "해당 없음 — 스윙은 진입 시가 대비 종가 -3%(다음 장 시가 매도)",
      muted: true,
    });
    expect(nearStopText({ near_stop: [] })).toEqual({ text: "없음", muted: true });
  });
});
