/**
 * /kis 칸(요약·보유내역·거래내역·성과)을 바꾸면 본문이 바뀐다 (docs/infra.md 25.1524).
 * 예전에는 "지금 KIS에서 조회" 결과 상자가 칸과 상관없이 칸 막대 밑에 붙어 폰에서 칸을 바꿔도 같은 화면으로 보였다.
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { KisBody } from "@/components/KisPanel";
import type { KisMarketView, KisOrder } from "@/lib/kisPaper";
import type { LiveView } from "@/lib/kisVts";

const v: KisMarketView = {
  as_of: "2026-10-16",
  fetched_at: "t",
  holdings: [{ id: 1, code: "B1", name: "배치보유", qty: 3, avg: 100, price: 110, value: 330, pnl: 30 }],
  account: { currency: "KRW", cash: 670, holdings_value: 330, total: 1000 },
  summary: null,
  diff: {},
  orders: [
    { id: 1, trade_date: "2026-10-15", code: "B2", name: "배치주문", side: "buy", quantity: 3, status: "filled", filled_qty: 3, avg_price: 100 } as unknown as KisOrder,
  ],
};
const live: LiveView = {
  market: "KR",
  currency: "KRW",
  fetched_at: "2026-10-16T01:00:00Z",
  account: { cash: 600, holdings_value: 400, total: 1000 },
  holdings: [{ code: "L1", name: "조회보유", qty: 4, avg: 100, price: 100, value: 400, pnl: 0, pnl_pct: 0 }],
  executions: [{ code: "L2", name: "조회체결", side: "buy", qty: 1, filled: 1, time: "0901", avg_price: 100, note: null }],
} as unknown as LiveView;

const 그림 = (sub: "summary" | "holdings" | "trades" | "perf", l: LiveView | null = live) =>
  renderToStaticMarkup(createElement(KisBody, { sub, market: "KR", v, whole: null, live: l }));

describe("KIS모의투자 칸 본문 (25.1524)", () => {
  it("칸마다 본문이 다르다 — 네 칸이 모두 다른 글", () => {
    const html = (["summary", "holdings", "trades", "perf"] as const).map((s) => 그림(s));
    expect(new Set(html).size).toBe(4);
    expect(new Set((["summary", "holdings", "trades", "perf"] as const).map((s) => 그림(s, null))).size).toBe(4);
  });

  it("방금 조회 결과가 칸별로 나뉜다 — 보유는 보유내역에만, 체결은 거래내역에만", () => {
    expect(그림("holdings")).toContain("조회보유");
    expect(그림("holdings")).toContain("배치보유");
    expect(그림("holdings")).not.toContain("조회체결");
    expect(그림("trades")).toContain("조회체결");
    expect(그림("trades")).toContain("배치주문");
    expect(그림("trades")).not.toContain("조회보유");
    for (const s of ["summary", "perf"] as const) {
      expect(그림(s)).not.toContain("조회보유");
      expect(그림(s)).not.toContain("조회체결");
    }
    expect(그림("summary")).toContain("보유 1종목 · 오늘 주문 1건");
    expect(그림("perf")).not.toContain("KIS에서 방금 조회");
  });

  it("조회 전이면 배치 기록만", () => {
    expect(그림("holdings", null)).not.toContain("KIS에서 방금 조회");
    expect(그림("holdings", null)).toContain("배치보유");
    expect(그림("trades", null)).toContain("배치주문");
  });
});
