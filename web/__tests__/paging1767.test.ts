/**
 * 거래내역 쪽 나누기·KIS 주문 시각 (2026-10-23 사용자 지시, docs/infra.md 25.1767).
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { KisBody } from "@/components/KisPanel";
import { TradesPane, type FillRow } from "@/components/PaperView";
import { sentTimeText, type KisMarketView, type KisOrder } from "@/lib/kisPaper";
import { PAGE_SIZE, pageOf, pageRangeText } from "@/lib/paging";

describe("쪽 나누기", () => {
  const 줄 = Array.from({ length: 45 }, (_, i) => i);

  it("20건씩, 마지막 쪽은 나머지", () => {
    expect(PAGE_SIZE).toBe(20);
    expect(pageOf(줄, 0)).toMatchObject({ page: 0, pages: 3, total: 45 });
    expect(pageOf(줄, 0).rows).toEqual(줄.slice(0, 20));
    expect(pageOf(줄, 2).rows).toEqual([40, 41, 42, 43, 44]);
    expect(pageRangeText(pageOf(줄, 1))).toBe("21–40 / 45건");
  });

  it("범위 밖 쪽은 끝으로 당기고, 빈 목록도 한 쪽", () => {
    expect(pageOf(줄, 9).page).toBe(2);
    expect(pageOf(줄, -1).page).toBe(0);
    expect(pageOf([], 0)).toEqual({ rows: [], page: 0, pages: 1, total: 0 });
    expect(pageRangeText(pageOf([], 0))).toBe("0건");
  });
});

describe("KIS 주문 시각", () => {
  it("보낸 시각을 한국 시각으로 — 거래일과 날짜가 다르면 날짜도", () => {
    expect(sentTimeText("2026-10-22T23:31:00Z", "2026-10-23")).toBe("08:31"); // 국내 장전 주문
    expect(sentTimeText("2026-10-22T13:05:00+00:00", "2026-10-23")).toBe("10-22 22:05"); // 전날 밤 미국 예약주문
    expect(sentTimeText(null, "2026-10-23")).toBe("-");
    expect(sentTimeText("깨짐", "2026-10-23")).toBe("-");
  });
});

const 주문 = (i: number): KisOrder =>
  ({
    id: i, trade_date: "2026-10-23", code: `C${i}`, name: `주문${String(i).padStart(2, "0")}`, side: "buy", order_type: "limit", limit_price: 100, quantity: 1,
    status: "filled", filled_qty: 1, avg_price: 100, reason: "베낌", message: null, sent_at: "2026-10-22T23:31:00Z", stock_id: i,
  }) as KisOrder;

describe("거래내역 칸이 쪽으로 나뉜다", () => {
  it("KIS모의투자 — 첫 쪽 20건, 쪽 넘기기, 주문 시각 열", () => {
    const v: KisMarketView = { as_of: null, fetched_at: null, holdings: [], account: null, summary: null, diff: {}, orders: Array.from({ length: 25 }, (_, i) => 주문(i + 1)) };
    const html = renderToStaticMarkup(createElement(KisBody, { sub: "trades", market: "KR", v, whole: null, live: null }));
    expect(html).toContain("주문01");
    expect(html).toContain("주문20");
    expect(html).not.toContain("주문21");
    expect(html).toContain("1 / 2쪽 · 1–20 / 25건");
    expect(html).toContain("주문 시각");
    expect(html).toContain(">08:31<");
  });

  it("한 쪽뿐이면 쪽 넘기기를 그리지 않는다", () => {
    const v: KisMarketView = { as_of: null, fetched_at: null, holdings: [], account: null, summary: null, diff: {}, orders: [주문(1)] };
    expect(renderToStaticMarkup(createElement(KisBody, { sub: "trades", market: "KR", v, whole: null, live: null }))).not.toContain("쪽 넘기기");
  });

  it("가상모의투자 — 첫 쪽 20건", () => {
    const 체결 = Array.from({ length: 23 }, (_, i) => ({
      id: i, account_id: "A", ticker: "T", name: `체결${String(i).padStart(2, "0")}`, side: "buy", fill_date: "2026-10-23", price: 100, quantity: 1, currency: "KRW",
      fx_rate: 1, fee: 0, tax: 0, slippage: 0, cash_krw: -100, realized_krw: null, reason: "신호", decided_on: "2026-10-22", rationale_json: null,
    })) as FillRow[];
    const html = renderToStaticMarkup(createElement(TradesPane, { fills: 체결 }));
    expect(html).toContain("체결19");
    expect(html).not.toContain("체결20");
    expect(html).toContain("1 / 2쪽 · 1–20 / 23건");
  });
});
