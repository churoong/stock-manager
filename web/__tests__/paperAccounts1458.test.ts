/** 모의 계좌 7개 (2026-10-15 사용자 지시 "두개의 모의투자자로는 부족할거 같다", docs/infra.md 25.1458) */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AccountTabs, CompareCard, type Data } from "@/components/PaperView";
import { FEE_BUFFER, OPEN_BUY_BUFFER, accountView, reservedByAccount, ruleText } from "@/lib/paper";

const 규칙 = [
  ["A", "all", "tranche", 5, "signal"],
  ["B", "core", "tranche", 5, "signal"],
  ["C", "core", "open", 5, "signal"],
  ["D", "time", "tranche", 5, "signal"],
  ["E", "core", "tranche", 10, "signal"],
  ["R1", "core", "open", 5, "random"],
  ["R2", "core", "open", 5, "random"],
] as const;

const data = {
  halted: false, realCompare: null, invested: 1_000_000, flows: [], history: [], fills: [],
  accounts: 규칙.map(([id, sell_rule, entry_rule, top_n, selection]) => ({
    id, name: `모의 ${id}`, sell_rule, entry_rule, top_n, selection, rule_version: 1, unknown: [], attribution: null, positions: [],
    view: accountView(id, 1_000_000, 1_000_000, 0, undefined),
  })),
  orders: [
    { id: 1, account_id: "R1", market: "KR", ticker: "000660", name: "SK하이닉스", side: "buy", order_type: "market", limit_price: null,
      ref_price: 200_000, quantity: 5, currency: "KRW", fill_session: "2026-10-19", tranche: 1, reason: "무작위 대조" },
  ],
} as unknown as Data;

describe("모의 계좌 7개", () => {
  it("규칙은 칸에서 글로 — 계좌 이름을 보지 않는다", () => {
    expect(ruleText({ sell_rule: "time", entry_rule: "tranche", top_n: 5, selection: "signal" })).toBe(
      "매수: 점수 상위 5종목 · 3회 분할 지정가 — 매도: 기간초과만(손절·목표 무시)",
    );
    expect(ruleText({ sell_rule: "core", entry_rule: "open", top_n: 5, selection: "random" })).toBe(
      "매수: 무작위 대조(같은 자리·비중) · 다음 장 시가에 한 번에 — 매도: 손절·목표도달·기간초과만",
    );
    expect(ruleText({ sell_rule: "core", top_n: 10 })).toContain("점수 상위 10종목 · 3회 분할 지정가");
  });

  it("시장가 매수도 예약에 든다 — 0 으로 세면 입금 취소가 C·R 에 묶인 돈을 못 본다 (검증 [필수] 5)", () => {
    const 예약 = reservedByAccount(
      [
        { account_id: "C", order_type: "market", limit_price: null, ref_price: 10_000, quantity: 10, currency: "KRW" },
        { account_id: "B", order_type: "limit", limit_price: 10_000, ref_price: null, quantity: 4, currency: "KRW" },
      ],
      null,
    );
    expect(예약.C).toBe(Math.round(10_000 * (1 + OPEN_BUY_BUFFER) * 10 * (1 + FEE_BUFFER)));
    expect(예약.B).toBe(Math.round(10_000 * 4 * (1 + FEE_BUFFER)));
  });

  it("계좌 비교 — 일곱 계좌와 짝의 뜻", () => {
    const html = renderToStaticMarkup(createElement(CompareCard, { data }));
    for (const id of ["A", "B", "C", "D", "E", "R1", "R2"]) expect(html).toContain(`>${id}<`);
    expect(html).toContain("C−R1: 종목 선정 대 무작위");
    expect(html).toContain("B−D: 매도 플래그 대 기간 보유");
    expect(html).toContain("기간초과만(손절·목표 무시)");
  });

  it("계좌 탭 일곱 개, 미체결의 시가 일괄 주문", () => {
    const html = renderToStaticMarkup(
      createElement(AccountTabs, { data, acct: "R1", setAcct: () => {}, sub: "orders", setSub: () => {} }),
    );
    for (const id of ["A", "B", "C", "D", "E", "R1", "R2"]) expect(html).toContain(`모의 ${id}`);
    expect(html).toContain("매수 시가 일괄 (기준가 200,000원 · 무작위 대조)");
  });
});
