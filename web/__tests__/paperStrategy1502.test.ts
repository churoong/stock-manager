/**
 * 전략 화면 (docs/desk.md 3장, docs/infra.md 25.1502) — 전략 순위·판 이력·안 산 것.
 * 결정 원장 질의는 실제 마이그레이션을 적용한 SQLite 에 돌리고, 화면은 서버 렌더로 글자를 본다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";
import { AccountTabs, CompareCard, type Data } from "@/components/PaperView";
import { EMPTY_DESK, pickDesk, ruleSpanText, type DeskView } from "@/lib/desk";
import { accountView } from "@/lib/paper";
import { DECISIONS_SQL, DECISION_LIMIT, afterText, beatBench, validAccount } from "@/lib/paperDecisions";

const MIGRATIONS = join(process.cwd(), "..", "migrations");

describe("안 산 것 — 결정 원장 질의", () => {
  let db: DatabaseSync;
  beforeEach(() => {
    db = new DatabaseSync(":memory:");
    for (const f of readdirSync(MIGRATIONS).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
    db.exec("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at) VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')");
    db.exec("INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at) VALUES (2, 'AAPL', 'NASDAQ', 'US', 'Apple', 'USD', 'active', 't', 't')");
  });

  it("그 계좌·시장의 안 한 결정만, 최근 것부터, 60줄까지", () => {
    const ins = db.prepare(
      "INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ret_20d, bench_20d, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 't')",
    );
    ins.run("A", "KR", "2026-10-16", 1, "skip", "점수 미달", 3.1, 1.2);
    ins.run("A", "KR", "2026-10-16", 1, "buy", "가결", null, null); // 산 것은 빼고
    ins.run("A", "KR", "2026-10-17", 1, "blocked", "위험 점검: 한 종목 상한", null, null);
    ins.run("A", "US", "2026-10-17", 2, "skip", "미국", null, null); // 다른 시장은 빼고
    ins.run("B", "KR", "2026-10-17", 1, "missed", "안 찬 회차", null, null); // 다른 계좌는 빼고
    const 날 = (i: number) => new Date(Date.UTC(2026, 10, 1) + i * 86_400_000).toISOString().slice(0, 10);
    for (let i = 0; i < 70; i++) ins.run("C", "KR", 날(i), 1, i % 2 ? "skip" : "hold_flag", `r${i}`, null, null);
    const rows = db.prepare(DECISIONS_SQL).all("A", "KR") as Array<{ decided_on: string; kind: string; name: string; ticker: string }>;
    expect(rows.map((r) => [r.decided_on, r.kind])).toEqual([["2026-10-17", "blocked"], ["2026-10-16", "skip"]]);
    expect(rows[0].name).toBe("삼성전자");
    expect((db.prepare(DECISIONS_SQL).all("C", "KR") as unknown[]).length).toBe(DECISION_LIMIT);
    // 질의 계획 — UNIQUE 색인(account_id, market, decided_on, …)으로 계좌·시장을 고정한다(표 전체를 훑지 않는다)
    const plan = (db.prepare(`EXPLAIN QUERY PLAN ${DECISIONS_SQL}`).all("A", "KR") as Array<{ detail: string }>).map((p) => p.detail).join(" | ");
    expect(plan).toMatch(/USING INDEX sqlite_autoindex_paper_decisions/);
  });

  it("그 뒤 글 — 가장 긴 찬 창, 지수 나란히(% 단위 그대로), 아직·닫힘", () => {
    const 빈 = { ret_5d: null, ret_20d: null, ret_60d: null, bench_5d: null, bench_20d: null, bench_60d: null, outcome_note: null };
    expect(afterText({ ...빈, ret_5d: 1.5, bench_5d: 0.5, ret_20d: 3.1, bench_20d: 1.2 })).toBe("20일 +3.1% (지수 +1.2%)");
    expect(afterText({ ...빈, ret_5d: -2 })).toBe("5일 -2.0%");
    expect(afterText(빈)).toBe("아직(결정 뒤 5 거래일 전)");
    expect(afterText({ ...빈, outcome_note: "시세 없음" })).toBe("성과 없음 — 시세 없음");
    expect(beatBench({ ...빈, ret_20d: 3.1, bench_20d: 1.2 })).toBe(true);
    expect(beatBench({ ...빈, ret_20d: 0.5, bench_20d: 1.2 })).toBe(false);
    expect(beatBench({ ...빈, ret_20d: 0.5 })).toBeNull();
    expect(beatBench(빈)).toBeNull();
  });

  it("계좌 이름은 대문자로 시작하는 1~4자만", () => {
    for (const ok of ["A", "R1", "H"]) expect(validAccount(ok)).toBe(true);
    for (const no of [null, "", "a", "1A", "ABCDE", "A;--"]) expect(validAccount(no)).toBe(false);
  });
});

const 평가 = (aid: string) => ({
  account_id: aid, as_of: "2026-10-20", invested_krw: 1_000_000, cash_krw: 1_000_000, holdings_krw: 0, equity_krw: 1_000_000,
  pnl_krw: 0, twr_index: 1, kospi_krw: null, sp500_krw: null, recon_ok: 1, recon_note: "맞음", updated_at: "t", realized_krw: 0, unrealized_krw: 0,
});
const 계좌 = (id: string) => ({
  id, name: `모의 ${id}`, sell_rule: "core", rule_version: 2, unknown: [], attribution: null, positions: [],
  view: accountView(id, 1_000_000, 1_000_000, 0, 평가(id)),
});
const data = {
  halted: false, realCompare: null, invested: 1_000_000, flows: [], history: [], fills: [], orders: [],
  accounts: [계좌("A"), 계좌("B"), 계좌("R1")],
} as unknown as Data;
const desk: DeskView = {
  ...EMPTY_DESK,
  as_of: "2026-10-20",
  leaderboard: {
    window_start: "2026-10-16", control_pct: -0.5,
    rows: [
      { id: "B", name: "모의 B", kind: "기준(점수 상위)", open: "2026-10-16", last: "2026-10-20", since_open_pct: 2, window_pct: 2, vs_r_pp: 2.5 },
      { id: "R1", name: "모의 R1", kind: "무작위 대조", open: "2026-10-16", last: "2026-10-20", since_open_pct: -0.5, window_pct: -0.5, vs_r_pp: null },
      { id: "A", name: "모의 A", kind: "회사", open: "2026-10-16", last: "2026-10-20", since_open_pct: -1, window_pct: -1, vs_r_pp: -0.5 },
    ],
  },
  rule_history: {
    A: [
      { version: 1, from: "2026-10-14", to: "2026-10-16", orders: 5, pct: 3, control_pct: 0, vs_r_pp: 3 },
      { version: 2, from: "2026-10-16", to: null, orders: 1234, pct: 1.2, control_pct: 0.4, vs_r_pp: 0.8 },
    ],
  },
};

describe("전략 순위·판 이력 화면", () => {
  it("전략 순위 — 운용 요약 순서로, 공통 구간·R 대비 칸", () => {
    const html = renderToStaticMarkup(createElement(CompareCard, { data, desk }));
    expect(html).toContain("전략 순위");
    expect(html).toContain("공통 구간 2026-10-16부터");
    expect(html.indexOf(">B<")).toBeLessThan(html.indexOf(">R1<"));
    expect(html.indexOf(">R1<")).toBeLessThan(html.indexOf(">A<"));
    expect(html).toContain("+2.50%p");
    // 요약을 못 읽으면 계좌 순, 칸은 "-"
    const 없이 = renderToStaticMarkup(createElement(CompareCard, { data }));
    expect(없이.indexOf(">A<")).toBeLessThan(없이.indexOf(">B<"));
  });

  it("판 이력 탭 — 판마다 구간·주문 수·시간가중·같은 기간 R", () => {
    const html = renderToStaticMarkup(createElement(AccountTabs, { data, acct: "A", setAcct: () => {}, sub: "rules", setSub: () => {}, desk }));
    expect(html).toContain("판 이력");
    expect(html).toContain("판 1 · 2026-10-14~2026-10-16 · 주문 5건 · 시간가중 +3.00% (같은 기간 R 평균 +0.00%, R 대비 +3.00%p)");
    expect(html).toContain("판 2 · 2026-10-16~지금 · 주문 1,234건");
    // 요약에 그 계좌가 없으면(주문 0) · 요약 자체가 없으면
    expect(renderToStaticMarkup(createElement(AccountTabs, { data, acct: "B", setAcct: () => {}, sub: "rules", setSub: () => {}, desk }))).toContain(
      "다음 일일 배치가 적은 뒤부터",
    );
    expect(ruleSpanText({ version: 3, from: "d", to: null, orders: 0, pct: null, control_pct: null, vs_r_pp: null })).toBe("판 3 · d~지금 · 주문 0건 · 시간가중 -");
  });

  it("안 산 것 탭은 열 때 읽는다(서버 렌더에서는 불러오는 중)", () => {
    const html = renderToStaticMarkup(createElement(AccountTabs, { data, acct: "A", setAcct: () => {}, sub: "skips", setSub: () => {}, desk }));
    expect(html).toContain("안 산 것");
    expect(html).toContain("불러오는 중");
  });

  it("25.1502 전 요약에는 판 이력이 없다 — 빈 것으로", () => {
    expect(pickDesk([{ as_of: "d", summary_json: JSON.stringify({ markets: {} }), updated_at: "u" }]).rule_history).toEqual({});
  });
});
