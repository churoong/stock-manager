// 25.2022 (결함 루프) — 위험판 손절 가까움: 스윙·단타는 기간 손절선으로 재지 않고 그 까닭 글을 보인다
import { describe, expect, it } from "vitest";
import { nearStopText } from "@/lib/desk";

describe("nearStopText", () => {
  it("가까운 보유·까닭 글·없음", () => {
    expect(nearStopText({ near_stop: [{ name: "가", unrealized_pct: -5.5, stop_pct: -7 }] })).toEqual({ text: "가 -5.5%(선 -7%)", muted: false });
    expect(nearStopText({ near_stop: [], near_stop_note: "해당 없음 — 스윙은 진입 시가 대비 종가 −3%(다음 장 시가 매도)" })).toEqual({
      text: "해당 없음 — 스윙은 진입 시가 대비 종가 −3%(다음 장 시가 매도)",
      muted: true,
    });
    expect(nearStopText({ near_stop: [] })).toEqual({ text: "없음", muted: true });
    // 25.2023 — 단타에 하룻밤 넘긴 보유가 남은 날은 경고(회색 아님)
    expect(nearStopText({ near_stop: [], near_stop_note: "하루 넘긴 단타 보유 1종목 — 매도 대기(단타잔여·하한가·정지로 미룬 짝 매도)", near_stop_warn: true }).muted).toBe(false);
  });
});
