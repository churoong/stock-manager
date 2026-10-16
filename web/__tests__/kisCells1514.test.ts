/**
 * KIS 체결 칸 · KIS모의 체결 기준 보정(근사·참고) (docs/paper.md 15.9, docs/desk.md 8장, docs/infra.md 25.1514).
 * 값은 배치가 내고 화면은 글만 만든다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompareCard, type Data } from "@/components/PaperView";
import { EMPTY_DESK, adjustCheckText, adjustText, pickDesk, type DeskView, type FillAdjustRow } from "@/lib/desk";
import { CELL_MIN_DAYS, QUALITY_SQL, cellRows, excessText, type Cell, type QualityRow } from "@/lib/kisQuality";
import { accountView } from "@/lib/paper";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
const 칸 = (over: Partial<Cell> = {}): Cell => ({
  n: 40, days: 25, both: 30, internal_only: 4, kis_only: 3, neither: 3, unknown: 0, miss: 11.76, extra: 50, n_price: 30,
  excess: 0.2, median: 0.18, se: 0.03, ...over,
});

describe("체결 칸 (/kis)", () => {
  it("요약의 cells 를 읽어 칸 이름 순으로 · 표본 적은 칸은 표시", () => {
    const db = new DatabaseSync(":memory:");
    for (const f of readdirSync(MIGRATIONS).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
    db.prepare(
      "INSERT INTO paper_broker_daily (broker, as_of, account_id, holdings_json, diff_json, source, fetched_at, summary_json) VALUES ('kis_vts', '2026-11-20', 'H', '[]', '{}', 'kis_vts', 't', ?)",
    ).run(JSON.stringify({ n: 1, cells: { sell_market: 칸(), buy_limit: 칸({ days: 5, excess: -1.5 }), buy_market: 칸() } }));
    const rows = db.prepare(QUALITY_SQL).all("kis_vts", "2026-06-01") as unknown as QualityRow[];
    const c = cellRows(rows[0]);
    expect(c.map((x) => [x.label, x.measured])).toEqual([
      ["시장가 매수", true],
      ["지정가 매수", false],
      ["시장가 매도", true],
    ]);
    expect(cellRows({ ...rows[0], cells: "{" })).toEqual([]);
    expect(cellRows({ ...rows[0], cells: null })).toEqual([]);
  });

  it("장부 가정 넘는 비용 글 — ± 는 표준오차 둘", () => {
    expect(excessText(칸())).toBe("+0.20% ±0.06 (중앙 +0.18%, 가격 n=30)");
    expect(excessText(칸({ excess: -1.5, se: null, median: -1.2 }))).toBe("-1.50% (중앙 -1.20%, 가격 n=30)");
    expect(excessText(칸({ excess: null }))).toBe("가격 표본 없음");
  });

  it("화면의 CELL_MIN_DAYS 는 배치 상수와 같다", () => {
    const py = readFileSync(join(process.cwd(), "..", "batch", "services", "broker_mirror.py"), "utf8");
    expect(Number(/^CELL_MIN_DAYS = ([0-9]+)/m.exec(py)?.[1])).toBe(CELL_MIN_DAYS);
  });
});

const 보정 = (over: Partial<FillAdjustRow> = {}): FillAdjustRow => ({
  from: "2026-10-01", fills: 12, turnover_pct: 80, adj_pp: 0.05, band_pp: 0.02, unmeasured_pct: 20, pi_pp: 0.01, pi_missing: 0,
  limit_buy_pct: 60, adjusted_pct: 0.96, close_call: false, ...over,
});

describe("KIS 체결 보정 (전략 순위)", () => {
  it("보정 글", () => {
    expect(adjustText(보정())).toEqual({ main: "+0.96%", sub: "−0.05%p ±0.02 · 지정가 개선 +0.01%p · 안 잰 회전 20%" });
    expect(adjustText(보정({ adj_pp: -0.1, pi_pp: 0, unmeasured_pct: 0, close_call: true })).sub).toBe("+0.10%p ±0.02 · 이웃과 뒤집힐 수 있음");
    expect(adjustText(보정({ adj_pp: 0, band_pp: 0, pi_pp: 0 }))).toEqual({ main: "-", sub: "잰 칸 없음 · 안 잰 회전 20%" });
    expect(adjustText(보정({ fills: 0, adj_pp: 0, band_pp: 0, pi_pp: 0, adjusted_pct: 1 })).sub).toBe("체결 없음");
    expect(adjustText(undefined)).toEqual({ main: "-", sub: "" });
    expect(adjustCheckText({ from: "2026-10-16", to: "2026-11-20", actual_pp: -0.1, explained_pp: -0.07, band_pp: 0.02, unmeasured_pct: 3 }, "H")).toBe(
      "2026-10-16~2026-11-20 KIS 실측 손익 차이 -0.10%p (H 평가 대비) · 칸으로 설명한 몫 -0.07%p ±0.02 · 안 잰 회전 3%",
    );
    expect(adjustCheckText(null, "H")).toBeNull();
  });

  it("요약에서 꺼내고 열을 그린다 — 25.1514 전 요약이면 열이 없다", () => {
    const v = pickDesk([
      { as_of: "d", summary_json: JSON.stringify({ markets: {}, fill_adjust: { rows: { H: 보정() }, h_check: null, min_days: 20 } }), updated_at: "u" },
    ]);
    expect(v.fill_adjust?.rows.H.adj_pp).toBe(0.05);
    expect(pickDesk([{ as_of: "d", summary_json: JSON.stringify({ markets: {}, fill_adjust: "x" }), updated_at: "u" }]).fill_adjust).toBeNull();
    const 평가 = {
      account_id: "H", as_of: "2026-10-20", invested_krw: 1, cash_krw: 1, holdings_krw: 0, equity_krw: 1, pnl_krw: 0, twr_index: 1,
      kospi_krw: null, sp500_krw: null, recon_ok: 1, recon_note: "", updated_at: "t", realized_krw: 0, unrealized_krw: 0,
    };
    const 계좌 = (id: string) => ({ id, name: id, sell_rule: "core", rule_version: 1, unknown: [], attribution: null, positions: [], view: accountView(id, 1, 1, 0, { ...평가, account_id: id }) });
    const data = { halted: false, realCompare: null, invested: 1, flows: [], history: [], fills: [], orders: [], accounts: [계좌("C"), 계좌("H")] } as unknown as Data;
    const desk: DeskView = { ...EMPTY_DESK, mirror_account: "H", fill_adjust: v.fill_adjust };
    const html = renderToStaticMarkup(createElement(CompareCard, { data, desk }));
    expect(html).toContain("KIS 체결 보정");
    expect(html).toContain("−0.05%p ±0.02");
    expect(html).toContain("순위는 바꾸지 않습니다");
    expect(renderToStaticMarkup(createElement(CompareCard, { data, desk: { ...desk, fill_adjust: null } }))).not.toContain("KIS 체결 보정");
    expect(renderToStaticMarkup(createElement(CompareCard, { data, desk: { ...desk, fill_adjust: null, fill_adjust_error: "RuntimeError: 망" } }))).toContain(
      "KIS 체결 보정 계산 실패 — RuntimeError: 망",
    );
  });
});

describe("검토 반영 (25.1516)", () => {
  it("잰 칸 없이 지정가 개선만 — ±0.00 으로 잰 척하지 않는다", () => {
    expect(adjustText(보정({ adj_pp: 0, band_pp: 0, pi_pp: 0.2, measured_pct: 0, unmeasured_pct: 0, adjusted_pct: 1.2 }))).toEqual({
      main: "+1.20%",
      sub: "잰 칸 없음 · 지정가 개선 +0.20%p",
    });
    expect(adjustText(보정({ measured_pct: 10 })).sub).toBe("−0.05%p ±0.02 · 지정가 개선 +0.01%p · 안 잰 회전 20%");
  });

  it("배치가 적은 measured 를 그대로 — 거래일만 넉넉한 칸은 잰 칸이 아니다", () => {
    const r = { as_of: "d", n: 1, match_pct: null, kept_pct: null, slip_buy: null, slip_sell: null, rejected: null, cells: JSON.stringify({ buy_market: 칸({ days: 25, se: null, measured: false }) }) };
    expect(cellRows(r)[0].measured).toBe(false);
  });

  it("대보기 — 연결 전 보유가 섞이면 적는다", () => {
    expect(adjustCheckText({ from: "a", to: "b", actual_pp: 0, explained_pp: 0, band_pp: 0, unmeasured_pct: 0, pre_link: 2 }, "H")).toContain(
      "연결 전 보유 2종목이 섞여 실측이 흔들림(판정에서 뺌)",
    );
  });
});
