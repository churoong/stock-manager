/** 모의 투자사 — 투자위원회 탭 (docs/company.md, docs/infra.md 25.1469) — 서버 렌더로 글자를 본다 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AccountTabs, CommitteePane, type Data } from "@/components/PaperView";
import { accountView, brokerLines, deptScoreText, hasCommittee, ruleText, stanceText, virtualText } from "@/lib/paper";

const 헌장 = {
  name: "가치투자사", philosophy: "싸고 튼튼한 회사를 산다", weights: { research: 2, quant: 0.5, governance: 1 },
  lead: "research", veto: ["governance"], threshold: 1, agenda: 20, top_n: 5, max_mdd: null,
};
const 회의록 = {
  memo: "가치투자사 위원회: 안건 2 · 가결 1 · 부결 1 · 거부 0 · 쟁점 1",
  items: [
    { stock_id: 1, ticker: "005930", vote: 2.1, favor: 2, issue: false, passed: true, approved: true, reason: "위원회 가결: 표 2.10 · 찬성 2",
      vetoed_by: [], minority: [], opinions: { research: [1, 0.8], quant: [1, 0.5], governance: [0, 0, "자료 없음 — 덮지 않음"] } },
    { stock_id: 3, ticker: "000660", vote: 0.2, favor: 1, issue: true, passed: false, approved: false,
      reason: "위원회: 쟁점에서 주관 부서(리서치부) 반대", vetoed_by: [], minority: ["quant"],
      opinions: { research: [-1, 0.6], quant: [1, 0.9], governance: [0, 0, "자료 없음"] } },
  ],
};
const 계좌 = {
  id: "A", name: "모의 A (가치투자사)", sell_rule: "value", rule_version: 2, entry_rule: "tranche", top_n: 5, selection: "signal",
  charter_json: JSON.stringify(헌장), unknown: [], attribution: null, positions: [], view: accountView("A", 1_000_000, 1_000_000, 0, undefined),
};

describe("투자위원회 탭", () => {
  it("부서 입장 글 — ▲찬성·▼반대 + 확신, · 중립, – 자료 없음", () => {
    expect(stanceText([1, 0.8])).toBe("▲80");
    expect(stanceText([-1, 0.6])).toBe("▼60");
    expect(stanceText([0, 0])).toBe("·");
    expect(stanceText([0, 0, "자료 없음"])).toBe("–");
  });

  it("헌장과 회의록 — 결정·소수의견·근거 펼침", () => {
    const html = renderToStaticMarkup(createElement(CommitteePane, {
      a: 계좌 as never,
      meetings: [{ account_id: "A", market: "KR", decided_on: "2026-10-16", charter_hash: "h", minutes_json: JSON.stringify(회의록) }],
      dossiers: [{ market: "KR", as_of: "2026-10-15", stock_id: 1, dossier_json: '{"broker":{"n":3}}' }],
    }));
    for (const 글 of ["가치투자사", "싸고 튼튼한 회사를 산다", "리서치부 2", "주관 리서치부", "거부권 공시·지배구조부", "KR · 2026-10-16 회의",
      "가치투자사 위원회: 안건 2", "▲80", "▼60", "가결·집행", "부결 — 쟁점에서 주관 부서(리서치부) 반대", "소수의견 퀀트부", "근거"]) {
      expect(html).toContain(글);
    }
  });

  it("헌장 없는 계좌(B·R)에는 탭이 없다", () => {
    const data = { halted: false, realCompare: null, invested: 1, flows: [], history: [], fills: [], orders: [], meetings: [], dossiers: [],
      accounts: [계좌, { ...계좌, id: "B", name: "모의 B", sell_rule: "core", rule_version: 1, charter_json: null }] } as unknown as Data;
    const 그림 = (acct: string) => renderToStaticMarkup(createElement(AccountTabs, { data, acct, setAcct: () => {}, sub: "committee", setSub: () => {} }));
    expect(그림("A")).toContain("투자위원회");
    expect(그림("A")).toContain("아직 회의록이 없습니다");
    expect(그림("B")).not.toContain("투자위원회");
  });

  it("부서 성적표 — 가름과 참고 표시 (25.1472)", () => {
    const 줄 = deptScoreText({ dept: "research", n_for: 40, n_against: 31, n_neutral: 5, avg_for: 0.021, avg_against: -0.012,
      spread: 0.033, hit_for: 55, hit_against: 60, enough: true });
    expect(줄).toBe("찬성 40건 +2.1%p · 반대 31건 -1.2%p · 가름 +3.3%p");
    const 적음 = deptScoreText({ dept: "quant", n_for: 3, n_against: 0, n_neutral: 1, avg_for: 0.01, avg_against: null, spread: null,
      hit_for: 100, hit_against: null, enough: false });
    expect(적음).toBe("찬성 3건 +1.0%p · 반대 0건 — · 가름 — (참고)");
    const html = renderToStaticMarkup(createElement(CommitteePane, {
      a: { ...계좌, attribution: { departments: [{ dept: "research", n_for: 40, n_against: 31, n_neutral: 5, avg_for: 0.021,
        avg_against: -0.012, spread: 0.033, hit_for: 55, hit_against: 60, enough: true }] } } as never,
      meetings: [], dossiers: [],
    }));
    expect(html).toContain("리서치부: 찬성 40건 +2.1%p");
  });

  it("지주사 H — 위원회 탭 없음, 규칙 글 (25.1482)", () => {
    const h = { id: "H", sell_rule: "trend", entry_rule: "tranche", top_n: 5, selection: "weighted",
      charter_json: '{"kind": "holding", "voters": {"A": "x"}}' };
    expect(hasCommittee(h)).toBe(false);
    expect(hasCommittee({ charter_json: "{}", selection: "signal" })).toBe(true);
    expect(ruleText(h as never)).toBe(
      "매수: 지주사 — 회사 추천을 성적 가중치로 합친 점수, 두 곳 이상 추천한 종목 상위 5 · 3회 분할 지정가 — 매도: 손절·기간초과 + 목표에 닿은 뒤 고점 대비 손절폭 하락 시(목표에서 안 팖)");
  });

  it("KIS모의투자 — 배치가 낸 비교 요약을 글로, 연결 전 안내 (25.1478)", () => {
    expect(brokerLines(null)[0]).toContain("아직 이 시장의 KIS모의투자 기록이 없습니다");
    const 줄 = brokerLines({ as_of: "2026-10-19", diff: { "1": [3, 0] }, summary: {
      days: 28, n: 6, match_pct: 50, both: 2, internal_only: 1, kis_only: 2, neither: 1, partial: 1, rejected: 1, unknown: 1, cleanup: 1,
      slip_buy: { n: 1, avg: 1 }, slip_sell: { n: 0, avg: null }, reject_reasons: { APBK0919: 1 } } });
    expect(줄).toEqual([
      "지난 28일 주문 6 · 체결 일치 50% (둘 다 참 2 · 가상만 참 1 · KIS만 참 2 · 둘 다 안 참 1) · 부분 체결 1",
      "가격(+ 면 가상모의투자가 낙관): 매수 +1.00% (n=1) · 매도 표본 없음",
      "거부 까닭: APBK0919 1",
      "거부 1 · 모름 1 · 정리 주문 1 · 잔고가 어긋난 종목 1 (2026-10-19 기준)",  // 배치 compare_lines 와 같은 끝 줄 (25.1483·25.1564)
    ]);
    // 거부만 있는 주 — "주문 없음" 이 아니라 거부를 보인다 (25.1564)
    const 거부만 = brokerLines({ as_of: "2026-10-20", diff: {}, summary: {
      days: 28, n: 0, match_pct: null, both: 0, internal_only: 0, kis_only: 0, neither: 0, partial: 0, rejected: 5, unknown: 0, cleanup: 0,
      excluded: 2, slip_buy: { n: 0, avg: null }, slip_sell: { n: 0, avg: null }, reject_reasons: { EGW00201: 5 } } });
    expect(거부만).toEqual([
      "체결을 견줄 주문은 아직 없습니다 — 거부·표본 밖 주문만 있습니다(아래).",
      "거부 까닭: EGW00201 5",
      "거부 5 · 표본 밖 2(가상의 판정 아닌 만료·장 열린 뒤 시장가) · 잔고가 어긋난 종목 0 (2026-10-20 기준)",
    ]);
  });

  it("가상 대조 — 무작위 20종목 안 분위, 표본 없으면 숨김 (25.1476)", () => {
    expect(virtualText({ n: 12, avg_pct: 61.4, top_half: 58.3 })).toBe(
      "같은 날 무작위 20종목 안 평균 61 분위 · 위쪽 절반 58% (n=12, 참고) — 무작위면 50");
    expect(virtualText({ n: 0, avg_pct: null, top_half: null })).toBeNull();
    expect(virtualText(undefined)).toBeNull();
  });
});
