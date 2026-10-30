/**
 * 거래내역 쪽 나누기·KIS 주문 시각 (2026-10-23 사용자 지시, docs/infra.md 25.1767).
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { KisBody } from "@/components/KisPanel";
import { TradesPane, type FillRow } from "@/components/PaperView";
import { sentTimeText, type KisMarketView, type KisOrder } from "@/lib/kisPaper";
import type { LiveView } from "@/lib/kisVts";
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
    expect(sentTimeText("2026-10-22T13:05:00.123456+00:00", "2026-10-22")).toBe("22:05"); // 미국 예약주문 — 배치가 적는 꼴(마이크로초)
    expect(sentTimeText("2026-10-23T15:05:00+00:00", "2026-10-23")).toBe("10-24 00:05"); // 재실행·지연 같은 드문 길만 날짜가 붙는다
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

  it("방금 조회의 오늘 주문 시각은 '주문' 시각이라고 적는다 — KIS ord_tmd 는 체결 시각이 아니다", () => {
    const v: KisMarketView = { as_of: null, fetched_at: null, holdings: [], account: null, summary: null, diff: {}, orders: [] };
    const live = {
      market: "KR", currency: "KRW", fetched_at: "2026-10-23T01:00:00Z", account: { cash: 0, holdings_value: 0, total: 0 }, holdings: [],
      executions: [{ date: "20261023", time: "093012", code: "C", name: "조회", side: "buy", qty: 1, filled: 1, avg_price: 100, order_price: 100, note: null }],
    } as unknown as LiveView;
    expect(renderToStaticMarkup(createElement(KisBody, { sub: "trades", market: "KR", v, whole: null, live }))).toContain("주문 09:30:12 · 조회");
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
    expect(html).not.toContain("최근 300건만");
    // 서버가 자른 300건이면 그렇다고 적는다(25.1767 교차검증 [권고])
    const 꽉 = Array.from({ length: 300 }, (_, i) => ({ ...체결[0], id: i })) as FillRow[];
    expect(renderToStaticMarkup(createElement(TradesPane, { fills: 꽉 }))).toContain("최근 300건만 보입니다");
  });

  it("거래내역 근거는 tradeCriteria 를 거친다 — 옛 스윙 꼴도 칸이 찬다, 깨진 근거는 화면을 죽이지 않는다(25.2002)", () => {
    const 하나 = (id: number, rationale_json: string) => ({
      id, account_id: "S", ticker: "T", name: `스윙${id}`, side: "sell", fill_date: "2026-10-30", price: 100, quantity: 1, currency: "KRW",
      fx_rate: 1, fee: 0, tax: 0, slippage: 0, cash_krw: 100, realized_krw: 0, reason: "스윙손절", decided_on: "2026-10-29", rationale_json,
    });
    const html = renderToStaticMarkup(createElement(TradesPane, { fills: [
      하나(1, JSON.stringify({ flags: [{ criteria: [{ name: "진입가", value: 10000, date: "2026-10-29" }] }] })),
      하나(2, JSON.stringify({ signal: { criteria: "깨짐" }, flags: "깨짐" })),
      // signal 없이 flags 만 깨진 기록 — 부르는 쪽 flags 막이가 받는다(25.2003, 25.2002 교차검증 [권고])
      하나(3, JSON.stringify({ flags: "깨짐" })),
      하나(4, JSON.stringify({ flags: [null, { criteria: "x" }] })),
    ] as FillRow[] }));
    expect(html).toContain("진입가");
    expect(html).toContain("10,000");
    expect(html).toContain("옛 기록 꼴");
    expect(html).toContain("스윙2");
    expect(html).toContain("스윙3");
    expect(html).toContain("스윙4");
  });
});
