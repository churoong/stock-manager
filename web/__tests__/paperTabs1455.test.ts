/** 모의 계좌별 보유내역·거래내역·평가손익 (2026-10-15 사용자 지시, docs/infra.md 25.1455) — 서버 렌더로 글자를 본다 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ACCOUNT_COLOR, AccountTabs, type Data, type SubTab } from "@/components/PaperView";
import { ACCOUNT_NAMES } from "@/lib/accountNames";
import { accountView } from "@/lib/paper";

const 평가 = (aid: string, realized: number, unrealized: number) => ({
  account_id: aid, as_of: "2026-10-20", invested_krw: 1_000_000, cash_krw: 900_000, holdings_krw: 110_000, equity_krw: 1_010_000,
  pnl_krw: 10_000, twr_index: 1.01, kospi_krw: null, sp500_krw: null, recon_ok: 1, recon_note: "맞음", updated_at: "t",
  realized_krw: realized, unrealized_krw: unrealized,
});

const 보유A = {
  stock_id: 1, ticker: "005930", name: "삼성전자", quantity: 10, currency: "KRW", cost_krw: 100_015, value_krw: 110_000,
  price_date: "2026-10-17", horizon: "short", avg_price: 10_001.5, close: 11_000, first_buy_date: "2026-10-16",
  unrealized_krw: 9_985, unrealized_pct: 9.98, weight_pct: 10.9,
};

const data = {
  halted: false, realCompare: null, invested: 1_000_000, flows: [], history: [],
  accounts: [
    { id: "A", name: "모의 A (모든 매도 플래그)", sell_rule: "all", rule_version: 1, unknown: [], attribution: null,
      view: accountView("A", 1_000_000, 900_000, 0, 평가("A", -5_000, 9_985)), positions: [보유A] },
    { id: "B", name: "모의 B (손절·목표·기간만)", sell_rule: "core", rule_version: 1, unknown: [], attribution: null,
      view: accountView("B", 1_000_000, 950_000, 0, 평가("B", 0, 0)), positions: [] },
  ],
  fills: [
    { id: 1, account_id: "A", ticker: "000660", name: "SK하이닉스", side: "sell", fill_date: "2026-10-19", price: 9_000, quantity: 4,
      currency: "KRW", fx_rate: 1, fee: 5, tax: 54, slippage: 36, cash_krw: 35_905, realized_krw: -5_000, reason: "손절",
      decided_on: "2026-10-16", rationale_json: null },
    { id: 2, account_id: "B", ticker: "035420", name: "NAVER", side: "buy", fill_date: "2026-10-16", price: 200_000, quantity: 1,
      currency: "KRW", fx_rate: 1, fee: 30, tax: 0, slippage: 0, cash_krw: -200_030, realized_krw: null, reason: "2부 배분",
      decided_on: "2026-10-15", rationale_json: null },
  ],
  orders: [
    { id: 9, account_id: "B", market: "KR", ticker: "035420", name: "NAVER", side: "buy", limit_price: 195_000, quantity: 1,
      currency: "KRW", fill_session: "2026-10-21", tranche: 2, reason: "2부 배분" },
  ],
} as unknown as Data;

function 그림(acct: string, sub: SubTab): string {
  return renderToStaticMarkup(createElement(AccountTabs, { data, acct, setAcct: () => {}, sub, setSub: () => {} }));
}

describe("모의 계좌 탭", () => {
  it("요약 — 총 손익·평가손익·실현손익을 따로", () => {
    const html = 그림("A", "summary");
    expect(html).toContain("평가손익 (보유분, 아직 안 판 것 · 해외는 환차 포함)");
    expect(html).toContain("9,985원");
    expect(html).toContain("실현손익 (판 것, 비용 반영 · 해외는 환차 포함)");
    expect(html).toContain("-5,000원");
    expect(html).toContain("가치 · 규칙서 판");
    expect(html).toContain(">기준<");
  });

  it("보유내역 — 평균단가·현재가·평가손익·비중·첫 매수", () => {
    const html = 그림("A", "holdings");
    for (const 글 of ["삼성전자", "10,002원", "11,000원", "2026-10-17", "9,985원", "+10.0%", "10.9%", "2026-10-16", "단기"]) {
      expect(html).toContain(글);
    }
    expect(그림("B", "holdings")).toContain("보유 종목이 없습니다");
  });

  it("실현손익 탭 — 기간을 고르고 그 기간의 매도를 서버에서 읽는다(25.1693)", () => {
    const html = 그림("A", "realized");
    for (const 글 of ["실현손익", "이번 달", "지난 달", "최근 30일", "올해", "전체", "직접", "읽는 중"]) expect(html).toContain(글);
  });

  it("거래내역은 그 계좌 것만 — 매도에는 실현손익", () => {
    const a = 그림("A", "trades");
    expect(a).toContain("SK하이닉스");
    expect(a).toContain("실현손익 -5,000원");
    expect(a).not.toContain("NAVER");
    const b = 그림("B", "trades");
    expect(b).toContain("NAVER");
    expect(b).not.toContain("SK하이닉스");
  });

  it("미체결은 그 계좌 것만", () => {
    expect(그림("B", "orders")).toContain("매수 2회차 195,000원 이하");
    expect(그림("A", "orders")).toContain("체결 대기 주문이 없습니다");
  });
});

describe("보유 손익 표시·그래프 색 (25.1604)", () => {
  it("달러 종목은 원화 % 와 종목 통화 % 를 함께, 원가로 둔 줄은 까닭", () => {
    const 미국 = { ...보유A, stock_id: 7, ticker: "AAPL", name: "Apple", currency: "USD", avg_price: 100, close: 92,
      unrealized_krw: 1_000, unrealized_pct: 1.2, unrealized_local_pct: -8.0, unknown_why: null };
    const 멈춤 = { ...보유A, stock_id: 8, ticker: "000001", name: "멈춘종목", price_date: null, close: null,
      unrealized_krw: 0, unrealized_pct: 0, unknown_why: "기업행위(멈춤)" };
    const d = { ...data, accounts: [{ ...data.accounts[0], positions: [미국, 멈춤] }] } as unknown as Data;
    const html = renderToStaticMarkup(createElement(AccountTabs, { data: d, acct: "A", setAcct: () => {}, sub: "holdings", setSub: () => {} }));
    expect(html).toContain("+1.2% 원화");
    expect(html).toContain("-8.0% USD(환차 뺌)");
    expect(html).toContain("원가로 평가 — 기업행위(멈춤)");
  });

  it("그래프 색은 계좌마다 다르고 R 만 회색", () => {
    const 색 = Object.values(ACCOUNT_COLOR);
    expect(new Set(색).size).toBe(색.length);
    expect(ACCOUNT_COLOR.R1).toBe("#9ca3af");
    expect(ACCOUNT_COLOR.G).not.toBe(ACCOUNT_COLOR.R1);
    // 이름이 있는 계좌는 모두 고정 색 — S·T 가 예비 색 차례에 맡겨져 있었다(25.1893)
    for (const id of Object.keys(ACCOUNT_NAMES)) expect(ACCOUNT_COLOR[id], id).toBeTruthy();
  });
});
