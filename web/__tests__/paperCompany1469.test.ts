/** 모의 투자사 — 투자위원회 탭 (docs/company.md, docs/infra.md 25.1469) — 서버 렌더로 글자를 본다 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AccountTabs, CommitteePane, type Data } from "@/components/PaperView";
import { accountView, deptScoreText, stanceText } from "@/lib/paper";

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
});
