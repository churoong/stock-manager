/** 텔레그램 봇 숫자 글자가 웹 보유 화면과 같다 (docs/infra.md 25.1345, 텔레그램 감사 #1·#3) */
import { describe, expect, it } from "vitest";
import { pnlText } from "@/lib/telegramQa";

const 기본 = { quantity: 1, avg_price: 1, cost: 100, unrealized_pnl: null, unrealized_price_pnl_krw: null,
  unrealized_fx_pnl_krw: null, price_date: "2026-10-13" };

describe("봇 숫자", () => {
  it("오늘 산 종목은 -0% 가 아니라 0.0%, 이익에는 +", () => {
    expect(pnlText({ ...기본, currency: "KRW", unrealized_pnl_krw: -0.4, cost_krw: 1000 })).toBe(" · 평가손익률 0.0%");
    expect(pnlText({ ...기본, currency: "KRW", unrealized_pnl_krw: 35, cost_krw: 1000 })).toBe(" · 평가손익률 +3.5%");
  });

  it("환차 0.3원은 +0원이 아니라 0원", () => {
    const t = pnlText({ ...기본, currency: "USD", unrealized_pnl: -10, unrealized_fx_pnl_krw: 0.3, unrealized_pnl_krw: 10, cost_krw: 1000 });
    expect(t).toContain("환차 0원");
    expect(t).toContain("주가 -10.0%");
  });
});
