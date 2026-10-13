// 국내 알림이 KIS 실시간이 아니면(야후 지연 시세로 물러남) 줄마다 지연과 시세 시각을 적는다 (docs/infra.md 25.1309)
import { describe, expect, it, vi } from "vitest";
vi.mock("@/lib/intradaySwitch", () => ({ INTRADAY_ENABLED: true, INTRADAY_OFF_NOTE: "꺼짐" }));
import { evaluate, KIS_QUOTE_SOURCE, type Quote, type Target } from "@/lib/intraday";
import { KIS_SOURCE } from "@/lib/kis";

const TH = { spike_pct: 5, volume_multiple: 3 };
const T: Target = {
  stock_id: 1, yahoo_symbol: "005930.KS", name: "삼성전자", currency: "KRW", reasons: ["holding"],
  buy_zone_low: null, buy_zone_high: null, target_price: null, stop_price: 65_100, prev_close: 70_000, avg_volume_20d: null,
};
// 10:05 KST 호출, 야후 시세는 09:45 KST
const Q: Quote = { symbol: "005930.KS", price: 64_900, time: "2026-10-13T00:45:00.000Z", previous_close: 70_000,
  day_high: 70_000, day_low: 64_800, volume: null };

describe("국내 지연 시세 표시", () => {
  it("야후로 물러나면 줄마다 지연과 시각", () => {
    const hits = evaluate(T, { ...Q, source: "yahoo_spark" }, TH);
    expect(hits.length).toBeGreaterThan(0);
    for (const h of hits) expect(h.message).toContain("(지연 시세 09:45 기준)");
  });
  it("KIS 실시간이면 붙이지 않는다", () => {
    for (const h of evaluate(T, { ...Q, source: KIS_QUOTE_SOURCE }, TH)) expect(h.message).not.toContain("지연");
  });
  it("미국은 머리줄이 이미 말한다 — 줄에는 붙이지 않는다", () => {
    for (const h of evaluate({ ...T, currency: "USD" }, { ...Q, source: "yahoo_spark" }, TH)) expect(h.message).not.toContain("지연");
  });
  it("출처 이름이 KIS 클라이언트와 같다", () => {
    expect(KIS_QUOTE_SOURCE).toBe(KIS_SOURCE);
  });
});

describe("시세 순서 (25.1310)", () => {
  it("보유 먼저, 신호, 관심 — 같은 종목은 가장 앞 순위로 한 번", async () => {
    const { quoteOrder } = await import("@/lib/intraday");
    expect(quoteOrder([
      { yahoo_symbol: "W.KS", reasons: ["watch"] },
      { yahoo_symbol: "S.KS", reasons: ["signal:mid"] },
      { yahoo_symbol: "H.KS", reasons: ["holding"] },
      { yahoo_symbol: "W.KS", reasons: ["holding"] },
    ])).toEqual(["W.KS", "H.KS", "S.KS"]);
  });
  it("route 가 이 순서로 묻는다", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync(new URL("../app/api/cron/intraday/route.ts", import.meta.url), "utf8");
    expect(src).toContain("const symbols = quoteOrder(");
  });
});
