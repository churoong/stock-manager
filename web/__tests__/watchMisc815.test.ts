/** 관심·설정 감사의 남은 하 (docs/infra.md 25.815) */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { KR_TARGETS } from "@/lib/newsKr";
import { NEXT_TARGET } from "@/lib/news";
import { QUOTE_SOURCE, evaluate } from "@/lib/intraday";

describe("관심·설정 남은 하", () => {
  it("관심 종목은 알림을 꺼도 뉴스를 모은다", () => {
    for (const sql of [KR_TARGETS, NEXT_TARGET]) {
      // 25.909 부터 미국은 우선순위를 붙여 `UNION ALL SELECT stock_id, 1 FROM watchlist` — 관심 전부가 후보인 것은 같다
      expect(sql).toMatch(/UNION (ALL )?SELECT stock_id(, 1)? FROM watchlist\n/);
      expect(sql).not.toContain("watchlist WHERE alert_enabled");
    }
  });

  it("알림 행에 출처가 남는다", () => {
    const hits = evaluate(
      { stock_id: 1, name: "A", currency: "KRW", reasons: ["held"], buy_zone_low: null, buy_zone_high: null, target_price: null, stop_price: 90 } as never,
      { symbol: "A", price: 80, time: "2026-10-01T01:00:00Z", previous_close: 100, day_high: 100, day_low: 80, volume: null },
      { spike_pct: 50, volume_multiple: 100 } as never,
    );
    expect(hits.length).toBeGreaterThan(0);
    expect(hits[0].data.source).toBe(QUOTE_SOURCE);
    // 공시 알림에도 출처 (25.817)
    expect(readFileSync("lib/intraday.ts", "utf-8")).toContain('source: "dart_list"');
  });

  it("관심 목록을 못 읽으면 추천 화면이 '모름' 을 알린다", () => {
    const 화면 = readFileSync("components/RecommendList.tsx", "utf-8");
    expect(화면).toContain("setWatchUnknown(ids === null)");
    expect(readFileSync("components/StockDetail.tsx", "utf-8")).toContain("if (ids === null) {");
  });
});

describe("감시 수 옆에 관심 수 (25.816)", () => {
  it("알림이 켜진 관심 종목 수를 나라별로 따로 적는다", () => {
    const 화면 = readFileSync("components/AlertCenter.tsx", "utf-8");
    expect(화면).toContain('watch.filter((w) => w.country === m && Number(w.alert_enabled) === 1).length');
    expect(화면).toContain("` · 관심 ${n}`");
  });
});

describe("알림 화면의 관심 빼기 (25.822)", () => {
  it("이미 빠진 종목은 '삭제 실패' 가 아니다", () => {
    expect(readFileSync("components/AlertCenter.tsx", "utf-8")).toContain("if (!r.ok && r.error !== WATCH_GONE) {");
  });
});
