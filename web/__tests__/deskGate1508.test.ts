/**
 * 실전 전환 관문·위험판·챔피언 교체 후보 화면 글 (docs/desk.md 6장, docs/infra.md 25.1508).
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompareCard, type Data } from "@/components/PaperView";
import { EMPTY_DESK, championText, gateNowText, pickDesk, type DeskView } from "@/lib/desk";
import { accountView } from "@/lib/paper";

describe("관문 칸 글", () => {
  it("칸마다 단위", () => {
    expect(gateNowText({ key: "days", label: "", now: 40, target: 126, ok: false })).toBe("40번 / 126번");
    expect(gateNowText({ key: "vs_r", label: "", now: 1.234, target: 0, ok: true })).toBe("+1.23%p / 0 초과");
    expect(gateNowText({ key: "ratio", label: "", now: 0.12345, target: null, ok: false })).toBe("0.123 / R 평균 모름");
    expect(gateNowText({ key: "incidents", label: "", now: 2, target: 0, ok: false, detail: { recon_bad_days: 1, batch_failed: 1 } })).toBe(
      "2건(검산 어긋난 날 1 · 배치 실패 1) / 0",
    );
    expect(gateNowText({ key: "kis", label: "", now: 91.6, target: 90, ok: false, n: 40 })).toBe("92% (표본 40) / 90% 이상");
    expect(gateNowText({ key: "kis", label: "", now: null, target: 90, ok: false })).toBe("모름");
  });

  it("25.1508 전 요약에는 관문·위험판·챔피언이 없다 — 빈 것으로", () => {
    const v = pickDesk([{ as_of: "d", summary_json: JSON.stringify({ markets: {} }), updated_at: "u" }]);
    expect([v.gate, v.risk, v.champion]).toEqual([null, [], []]);
  });
});

describe("챔피언 교체 후보 (전략 화면)", () => {
  const 평가 = (aid: string) => ({
    account_id: aid, as_of: "2026-10-20", invested_krw: 1, cash_krw: 1, holdings_krw: 0, equity_krw: 1, pnl_krw: 0, twr_index: 1,
    kospi_krw: null, sp500_krw: null, recon_ok: 1, recon_note: "", updated_at: "t", realized_krw: 0, unrealized_krw: 0,
  });
  const 계좌 = (id: string) => ({ id, name: id, sell_rule: "core", rule_version: 1, unknown: [], attribution: null, positions: [], view: accountView(id, 1, 1, 0, 평가(id)) });
  const data = { halted: false, realCompare: null, invested: 1, flows: [], history: [], fills: [], orders: [], accounts: [계좌("C"), 계좌("H")] } as unknown as Data;
  const 줄 = (candidate: boolean) => ({ id: "C", from: "2026-01-01", blocks: 3, wins: candidate ? 3 : 1, pct: 12, champion_pct: 6, candidate });

  it("후보가 있으면 초록 줄, 없으면 가장 가까운 것", () => {
    const desk: DeskView = { ...EMPTY_DESK, mirror_account: "H", champion: [줄(true)] };
    expect(renderToStaticMarkup(createElement(CompareCard, { data, desk }))).toContain("C · 블록 3개 중 3개 이김 · 같은 기간 +12.00% (H +6.00%) · 2026-01-01부터");
    const 없음 = renderToStaticMarkup(createElement(CompareCard, { data, desk: { ...desk, champion: [줄(false)] } }));
    expect(없음).toContain("아직 없음 — 가장 가까운 C · 블록 3개 중 1개 이김");
    expect(없음).toContain("교체는 사용자 결정");
    expect(championText(줄(true), "H")).toBe("C · 블록 3개 중 3개 이김 · 같은 기간 +12.00% (H +6.00%) · 2026-01-01부터");
  });
});
