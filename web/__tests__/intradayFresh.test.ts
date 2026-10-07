/** 감시 목록이 이번 장 것이 아니면 신호 값을 쓰지 않는다 (docs/infra.md 25.534, 감사 재현). */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { evaluate, targetsFreshFor, withoutSignalLevels, type Session, type Target } from "@/lib/intraday";

const 장: Session = { date: "2026-09-28", open_utc: "2026-09-28T00:00:00Z", close_utc: "2026-09-28T06:30:00Z" };

describe("감시 목록 신선도 (25.534)", () => {
  it("오늘 아침 목록은 이번 장, 어제 밤 목록은 아니다", () => {
    expect(targetsFreshFor("2026-09-27T23:27:00Z", 장)).toBe(true); // 08:27 KST
    expect(targetsFreshFor("2026-09-26T23:30:00Z", 장)).toBe(false); // 어제 아침
    expect(targetsFreshFor(null, 장)).toBe(false);
  });

  it("직전 세션이 닫힌 뒤 만든 목록은 저녁 재실행이어도 이번 장 것이다 (25.637)", () => {
    const 직전닫힘 = "2026-09-25T06:30:00Z"; // 금요일 15:30 KST
    expect(targetsFreshFor("2026-09-25T10:00:00Z", 장, 직전닫힘)).toBe(true); // 금요일 19:00 KST 재실행
    expect(targetsFreshFor("2026-09-25T05:00:00Z", 장, 직전닫힘)).toBe(false); // 금요일 장중 — 그 장 것
    expect(targetsFreshFor("2026-09-25T10:00:00Z", 장)).toBe(false); // 직전 세션을 모르면 예전 12시간 창
  });

  it("묵은 목록이면 어제 추천의 매수 구간 알림을 내지 않고 보유 손절은 그대로 본다", () => {
    const 신호: Target = {
      stock_id: 1, yahoo_symbol: "A.KS", name: "가", currency: "KRW", reasons: ["signal:short"],
      buy_zone_low: 9500, buy_zone_high: 10000, target_price: 11000, stop_price: 9000, prev_close: 10200, avg_volume_20d: null,
    };
    const 시세 = { symbol: "A.KS", price: 9800, time: "2026-09-28T00:05:00Z", previous_close: 10200, day_high: 9900, day_low: 8900, volume: null };
    const 문턱 = { spike_pct: 50, volume_multiple: 3 };
    expect(evaluate(신호, 시세, 문턱).map((h) => h.trigger)).toContain("buy_zone");
    expect(evaluate(withoutSignalLevels(신호), 시세, 문턱)).toEqual([]);
    const 보유 = { ...신호, reasons: ["holding", "signal:short"] };
    expect(evaluate(withoutSignalLevels(보유), 시세, 문턱).map((h) => h.trigger)).toEqual(["stop"]);
  });

  it("경로가 그 판정을 쓴다", () => {
    const 경로 = readFileSync("app/api/cron/intraday/route.ts", "utf8");
    expect(경로).toContain("targetsFreshFor(만든때, session, 직전닫힘)");
    expect(경로).toContain("withoutSignalLevels(풀린)");
  });
});
