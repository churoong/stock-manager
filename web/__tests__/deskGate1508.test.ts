/**
 * 실전 전환 관문·위험판·챔피언 교체 후보 화면 글 (docs/desk.md 6장, docs/infra.md 25.1508).
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ReadinessList } from "@/components/DeskView";
import { CompareCard, type Data } from "@/components/PaperView";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { DESK_RULES, EMPTY_DESK, GATE_CARRY_ERROR_HEAD, championText, gateNowText, pickDesk, type DeskView } from "@/lib/desk";
import { accountView } from "@/lib/paper";

describe("관문 칸 글", () => {
  it("칸마다 단위", () => {
    expect(gateNowText({ key: "days", label: "", now: 40, target: 126, ok: false })).toBe("40번 / 126번");
    expect(gateNowText({ key: "vs_r", label: "", now: 1.234, target: 0, ok: true })).toBe("+1.23%p / 0 초과");
    expect(gateNowText({ key: "ratio", label: "", now: 0.12345, target: null, ok: false })).toBe("0.123 / R 평균 모름");
    expect(gateNowText({ key: "incidents", label: "", now: 2, target: 0, ok: false, detail: { recon_bad_days: 1, batch_failed: 1 } })).toBe(
      "2일(검산 어긋난 날 1 · 배치 사고 날 1) / 0",
    );
    expect(gateNowText({ key: "kis", label: "", now: 91.6, target: 90, ok: false, n: 40 })).toBe("91.6% (표본 40) / 90% 이상");
    expect(gateNowText({ key: "kis", label: "", now: 89.6, target: 90, ok: false, n: 120 })).toBe("89.6% (표본 120) / 90% 이상"); // 반올림으로 "90%" 가 안 되게
    expect(gateNowText({ key: "kis", label: "", now: null, target: 90, ok: false })).toBe("모름");
  });

  it("25.1508 전 요약에는 관문·위험판·챔피언이 없다 — 빈 것으로", () => {
    const v = pickDesk([{ as_of: "d", summary_json: JSON.stringify({ markets: {} }), updated_at: "u" }]);
    expect([v.gate, v.risk, v.champion, v.gate_error]).toEqual([null, [], [], null]);
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

  it("이어받기 읽기만 깨진 날은 챔피언을 비운 것으로 적지 않는다 (25.1975)", () => {
    const desk: DeskView = { ...EMPTY_DESK, mirror_account: "H", champion: [] };
    const 앞오류 = renderToStaticMarkup(createElement(CompareCard, { data, desk: { ...desk, gate_error: `${GATE_CARRY_ERROR_HEAD} — OperationalError: x` } }));
    expect(앞오류).not.toContain("챔피언 교체 후보를 내지 않음");
    const 판오류 = renderToStaticMarkup(createElement(CompareCard, { data, desk: { ...desk, gate_error: "판 이력을 읽지 못해 관문을 내지 않음 — x" } }));
    expect(판오류).toContain("챔피언 교체 후보를 내지 않음");
    // 관문 계산도 깨진 날(챔피언을 비움)은 머리가 다르다 — 띄운다
    const 둘 = renderToStaticMarkup(createElement(CompareCard, { data, desk: { ...desk, gate_error: `관문 계산 실패 — RuntimeError: x · ${GATE_CARRY_ERROR_HEAD} — y` } }));
    expect(둘).toContain("챔피언 교체 후보를 내지 않음");
    const py = readFileSync(join(process.cwd(), "..", "batch", "jobs", "desk.py"), "utf8");
    expect(/^GATE_CARRY_ERROR_HEAD = "(.+)"$/m.exec(py)?.[1]).toBe(GATE_CARRY_ERROR_HEAD);
  });

  it("후보가 있으면 초록 줄, 없으면 가장 가까운 것", () => {
    const desk: DeskView = { ...EMPTY_DESK, mirror_account: "H", champion: [줄(true)] };
    expect(renderToStaticMarkup(createElement(CompareCard, { data, desk }))).toContain("모멘텀 · 블록 3개 중 3개 이김 · 같은 기간 +12.00% (지주사 +6.00%) · 2026-01-01부터");
    const 없음 = renderToStaticMarkup(createElement(CompareCard, { data, desk: { ...desk, champion: [줄(false)] } }));
    expect(없음).toContain("아직 없음 — 가장 가까운 모멘텀 · 블록 3개 중 1개 이김");
    expect(없음).toContain("교체는 사용자 결정");
    expect(championText(줄(true), "H")).toBe("모멘텀 · 블록 3개 중 3개 이김 · 같은 기간 +12.00% (지주사 +6.00%) · 2026-01-01부터");
  });
});

describe("화면 글의 문턱은 배치 상수와 같다 (25.1511)", () => {
  it("desk.py 의 RISK_NEAR_STOP_PP·CHAMPION_*", () => {
    const py = readFileSync(join(process.cwd(), "..", "batch", "services", "desk.py"), "utf8");
    const 값 = (name: string) => Number(new RegExp(`^${name} = ([0-9.]+)`, "m").exec(py)?.[1]);
    expect(값("RISK_NEAR_STOP_PP")).toBe(DESK_RULES.RISK_NEAR_STOP_PP);
    expect(값("CHAMPION_BLOCK")).toBe(DESK_RULES.CHAMPION_BLOCK);
    expect(값("CHAMPION_MIN_BLOCKS")).toBe(DESK_RULES.CHAMPION_MIN_BLOCKS);
    expect(값("CHAMPION_WIN_SHARE")).toBe(DESK_RULES.CHAMPION_WIN_SHARE);
    expect(값("GATE_SEL_MIN_BLOCKS")).toBe(DESK_RULES.GATE_SEL_MIN_BLOCKS);
    expect(값("CHAMPION_EVIDENCE_MIN_BLOCKS")).toBe(DESK_RULES.CHAMPION_EVIDENCE_MIN_BLOCKS);
    expect(값("CHAMPION_ALPHA")).toBe(DESK_RULES.CHAMPION_ALPHA);
    expect(값("GATE_POCOCK_ALPHA")).toBe(DESK_RULES.GATE_POCOCK_ALPHA);
  });
});

describe("실전 준비 목록 (25.1513)", () => {
  it("상태 말·근거·갖춤 수", () => {
    const html = renderToStaticMarkup(
      createElement(ReadinessList, {
        items: [
          { key: "audit", label: "감사 기록", status: "done", note: "결정 원장", evidence: null },
          { key: "broker", label: "증권사 주문 연동", status: "rehearsal", note: "KIS모의투자", evidence: "최근 28일 끝난 베낀 주문 35" },
        ],
      }),
    );
    expect(html).toContain("갖춤 1/2");
    expect(html).toContain("[모의로 연습 중]</span> 증권사 주문 연동");
    expect(html).toContain("근거: 최근 28일 끝난 베낀 주문 35");
    expect(pickDesk([{ as_of: "d", summary_json: "{}", updated_at: "u" }]).readiness).toEqual([]);
  });
});
