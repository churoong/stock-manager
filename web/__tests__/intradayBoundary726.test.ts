import { describe, expect, it } from "vitest";
import { evaluate } from "@/lib/intraday";

/** 부동소수 경계에서 정확히 닿은 경우를 놓쳤다 (docs/infra.md 25.726, 감사 재현) */
describe("경계 여유", () => {
  const 대상 = (over: Record<string, unknown>) => ({
    stock_id: 1, ticker: "005930", market: "KOSPI", currency: "KRW", name: "삼성전자", reasons: ["보유"],
    buy_zone_low: null, buy_zone_high: null, target_price: null, stop_price: null, prev_close: null, avg_volume_20d: null,
    ...over,
  });
  const 시세 = (over: Record<string, unknown>) => ({
    symbol: "005930.KS", price: 70_000, time: "2026-09-30T02:00:00.000Z", previous_close: null, day_high: null, day_low: null, volume: null,
    ...over,
  });
  const 문턱 = { spike_pct: 10, volume_multiple: 3 };
  it("손절가 65,099.999… 에 저가 65,100 은 터치다", () => {
    const hits = evaluate(대상({ stop_price: 70_000 * (1 - 0.07) }) as never, 시세({ price: 65_500, day_low: 65_100 }) as never, 문턱 as never);
    expect(hits.map((h: { trigger: string }) => h.trigger)).toContain("stop");
  });
  it("정확히 −10% 는 급락이다", () => {
    const hits = evaluate(대상({}) as never, 시세({ price: 63_000, previous_close: 70_000 }) as never, 문턱 as never);
    expect(hits.map((h: { trigger: string }) => h.trigger)).toContain("spike_down");
  });
});
