import { describe, expect, it } from "vitest";
import { periodRange, realizedReport, validRange, type RealizedRow } from "@/lib/paperRealized";

// docs/infra.md 25.1693 — 가상모의투자 기간 실현손익·일자별 실현손익(2026-10-22 사용자 지시)
const 줄 = (id: number, d: string, sid: number, v: number | null, cur = "KRW", fx: number | null = 0): RealizedRow => ({
  id, fill_date: d, stock_id: sid, ticker: `T${sid}`, name: `종목${sid}`, currency: cur, quantity: 1, price: 1,
  fee: 1, tax: 2, slippage: 0, realized_krw: v, realized_fx_krw: fx,
});

describe("기간", () => {
  it("이번 달·지난 달·최근 30일·올해·전체", () => {
    expect(periodRange("this_month", "2026-10-22")).toEqual(["2026-10-01", "2026-10-22"]);
    expect(periodRange("last_month", "2026-10-22")).toEqual(["2026-09-01", "2026-09-30"]);
    expect(periodRange("last_month", "2027-01-05")).toEqual(["2026-12-01", "2026-12-31"]);
    expect(periodRange("last_month", "2028-03-10")).toEqual(["2028-02-01", "2028-02-29"]); // 윤년
    expect(periodRange("d30", "2026-10-22")).toEqual(["2026-09-23", "2026-10-22"]);
    expect(periodRange("this_year", "2026-10-22")).toEqual(["2026-01-01", "2026-10-22"]);
    expect(periodRange("all", "2026-10-22")[0] <= "2026-10-15").toBe(true);
  });
  it("직접 — 꼴이 틀리면 오늘 하루", () => {
    expect(periodRange("custom", "2026-10-22", { from: "2026-10-01", to: "2026-10-10" })).toEqual(["2026-10-01", "2026-10-10"]);
    expect(periodRange("custom", "2026-10-22", { from: "10월", to: "x" })).toEqual(["2026-10-22", "2026-10-22"]);
    expect(validRange("2026-10-01", "2026-10-10")).toBe(true);
    expect(validRange("2026-10-10", "2026-10-01")).toBe(false);
    expect(validRange("2026-10-1", "2026-10-10")).toBe(false);
  });
});

describe("기간 실현손익", () => {
  const rows = [
    줄(3, "2026-10-16", 1, -5_000),
    줄(1, "2026-10-15", 1, 10_000),
    줄(2, "2026-10-15", 2, 3_000, "USD", 1_200),
    줄(4, "2026-10-16", 2, 2_000, "USD", -500),
    줄(5, "2026-10-17", 3, null), // 팔 때 장부 보유 없음 — 합에 넣지 않는다
  ];
  const r = realizedReport(rows);
  it("합·이긴 수·환차와 주가 몫", () => {
    expect(r.n).toBe(4);
    expect(r.realized).toBe(10_000);
    expect(r.fx).toBe(700);
    expect(r.price).toBe(9_300);
    expect([r.wins, r.losses, r.unknown, r.fxUnknown]).toEqual([3, 1, 1, 0]);
    expect(r.hasForeign).toBe(true);
    expect(r.costs).toEqual({ KRW: 9, USD: 6 });
  });
  it("일자별 — 날짜 순, 누적", () => {
    expect(r.byDay.map((d) => [d.key, d.n, d.realized, d.fx, d.cumulative])).toEqual([
      ["2026-10-15", 2, 13_000, 1_200, 13_000],
      ["2026-10-16", 2, -3_000, -500, 10_000],
    ]);
  });
  it("종목별 — 실현손익 큰 순", () => {
    expect(r.byStock.map((s) => [s.label, s.n, s.realized])).toEqual([
      ["종목1 (T1)", 2, 5_000],
      ["종목2 (T2)", 2, 5_000],
    ]);
  });
  it("환차 몫을 아직 모르는 해외 매도가 있으면 주가 몫도 모름", () => {
    const x = realizedReport([줄(1, "2026-10-15", 2, 3_000, "USD", null)]);
    expect(x.fxUnknown).toBe(1);
    expect(Number.isNaN(x.price)).toBe(true);
    expect(realizedReport([]).byDay).toEqual([]);
  });
});
