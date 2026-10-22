import { describe, expect, it } from "vitest";
import { countCell, realizedCell } from "@/components/PaperRealized";
import { realizedReport, type RealizedRow } from "@/lib/paperRealized";

// docs/infra.md 25.1722 — 실현손익을 모르는 매도를 "매도 없음 · 0건 · 0원" 으로 보이던 것(웹 감사 [필수] 1)
const 줄 = (id: number, sid: number, ticker: string, v: number | null, cur = "USD"): RealizedRow => ({
  id, fill_date: "2026-10-20", stock_id: sid, ticker, name: `이름${ticker}`, currency: cur, quantity: 1, price: 1,
  fee: 0, tax: 0, slippage: 0, realized_krw: v, realized_fx_krw: v == null ? null : 0,
});

describe("모르는 실현손익", () => {
  it("기간 매도가 모두 모름이면 아는 것 0건·모름 3건, 날·종목 표에도 모름으로", () => {
    const r = realizedReport([줄(1, 2, "AAPL", null), 줄(2, 2, "AAPL", null), 줄(3, 5, "MSFT", null)]);
    expect([r.n, r.unknown]).toEqual([0, 3]);
    expect(r.byDay.map((d) => [countCell(d), realizedCell(d)])).toEqual([["0 + 모름 3", "모름"]]);
    expect(r.byStock.map((s) => [s.label, countCell(s), realizedCell(s)])).toEqual([
      ["이름AAPL (AAPL)", "0 + 모름 2", "모름"],
      ["이름MSFT (MSFT)", "0 + 모름 1", "모름"],
    ]);
  });
  it("아는 것과 섞이면 아는 것만 더한 값과 모름 수", () => {
    const r = realizedReport([줄(1, 2, "AAPL", 30_000), 줄(2, 2, "AAPL", null)]);
    expect(r.byStock.map((s) => [countCell(s), realizedCell(s)])).toEqual([["1 + 모름 1", "30,000원"]]);
  });
});

describe("KIS 종목별은 티커로 묶는다 (25.1722, 웹 감사 [권고] 2)", () => {
  it("같은 티커의 두 종목 행(거래소 이전)이 한 줄", () => {
    const rows = [줄(1, 2, "AAPL", 30_000), 줄(2, 9, "AAPL", 20_000)];
    expect(realizedReport(rows, true).byStock.map((s) => [s.n, s.realized])).toEqual([[2, 50_000]]);
    // 가상모의투자는 종목 행 그대로
    expect(realizedReport(rows).byStock.length).toBe(2);
  });
});

describe("운용 화면 시각은 한국 시간 (25.1722, 웹 감사 [권고] 5)", () => {
  it("UTC 저녁 실행이 한국 날짜로", async () => {
    const { when } = await import("@/components/DeskView");
    expect(when("2026-10-22T23:31:00Z")).toContain("26. 10. 23.");
    expect(when(null)).toBe("-");
  });
});
