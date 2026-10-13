import { describe, expect, it } from "vitest";
import { evaluate, type Target } from "@/lib/intraday";

// 손절선에 닿은 날은 매수 구간·분할 매수 알림을 거른다 (docs/infra.md 25.1263, 신호 감사 재현)
const 문턱 = { spike_pct: 50, volume_multiple: 100 };
const 시세 = (price: number, low: number) => ({
  symbol: "A.KS",
  price,
  time: "2026-10-13T01:00:00Z",
  previous_close: price,
  day_high: price,
  day_low: low,
  volume: null,
});

describe("손절과 매수 알림", () => {
  it("합친 구간 안이어도 현재가가 손절선 아래면 매수 구간 알림을 내지 않는다", () => {
    // 단기 97,900~101,800 손절 92,900 + 장기 65,100~103,000 손절 63,000 → 합친 구간 65,100~103,000, 손절 92,900
    const t: Target = {
      stock_id: 1,
      yahoo_symbol: "A.KS",
      name: "가",
      currency: "KRW",
      reasons: ["signal:short", "signal:long"],
      buy_zone_low: 65_100,
      buy_zone_high: 103_000,
      target_price: 112_000,
      stop_price: 92_900,
      prev_close: 90_000,
      avg_volume_20d: null,
    };
    expect(
      evaluate(t, 시세(90_000, 90_000), 문턱).map((h) => h.trigger),
    ).toEqual(["stop"]);
    expect(
      evaluate(t, 시세(95_000, 95_000), 문턱).map((h) => h.trigger),
    ).toEqual(["buy_zone"]);
  });

  it("다음 분할 차수가 보유 손절선 아래면 손절만 낸다", () => {
    const t: Target = {
      stock_id: 1,
      yahoo_symbol: "A.KS",
      name: "가",
      currency: "KRW",
      reasons: ["holding"],
      buy_zone_low: null,
      buy_zone_high: null,
      target_price: 140_325,
      stop_price: 70_162.5,
      prev_close: 66_000,
      avg_volume_20d: null,
      next_tranche_price: 65_100,
      next_tranche_step: 3,
    };
    expect(
      evaluate(t, 시세(65_500, 65_000), 문턱).map((h) => h.trigger),
    ).toEqual(["stop"]);
  });
});
