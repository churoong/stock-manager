// 단타·스윙 그림자 장부 칸 (docs/paper.md 16.2, docs/infra.md 25.1875) — 설정 행을 읽어 나라별 집계로
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ShadowCard } from "@/components/PaperView";
import { SHADOW_READ, SHADOW_RULE_TEXT, shadowLedgers } from "@/lib/paper";

const 집계 = (as_of: string) =>
  JSON.stringify({
    as_of,
    stats: {
      day_v1: { n: 2, open: 0, no_entry: 1, stocks: 2, win_rate: 0.5, avg_net: 0.005, median_net: 0.005, profit_factor: 2, avg_sessions: 1 },
      swing_close_v1: { n: 0, open: 1, no_entry: 0, win_rate: null, avg_net: null, median_net: null, profit_factor: null, avg_sessions: null },
      swing_v9: { n: 0, open: 0, no_entry: 0, win_rate: null, avg_net: null, median_net: null, profit_factor: null, avg_sessions: null },
    },
  });

describe("그림자 장부 읽기", () => {
  it("두 설정 키만 읽는다", () => {
    expect(SHADOW_READ).toContain("'shadow_ledger_kr'");
    expect(SHADOW_READ).toContain("'shadow_ledger_us'");
  });
  it("나라마다 기준일과 규칙 글, 모르는 판 이름은 그대로", () => {
    const out = shadowLedgers([
      { key: "shadow_ledger_us", value: 집계("2026-11-03") },
      { key: "shadow_ledger_kr", value: 집계("2026-11-02") },
    ]);
    expect(out.map((l) => [l.market, l.asOf])).toEqual([["KR", "2026-11-02"], ["US", "2026-11-03"]]);
    expect(out[0].stats.map((x) => x.text)).toEqual([SHADOW_RULE_TEXT.day_v1, SHADOW_RULE_TEXT.swing_close_v1, "swing_v9"]);
    expect(out[0].stats[0].s.n).toBe(2);
  });
  it("없거나 깨진 값·빈 집계는 뺀다 — 화면은 '아직 없음'", () => {
    expect(shadowLedgers([])).toEqual([]);
    expect(shadowLedgers([{ key: "shadow_ledger_kr", value: "{깨짐" }, { key: "shadow_ledger_us", value: JSON.stringify({ stats: {} }) }])).toEqual([]);
  });
});

describe("그림자 장부 칸", () => {
  it("실제 스키마에서 질의가 돈다", () => {
    const db = new DatabaseSync(":memory:");
    const dir = join(process.cwd(), "..", "migrations");
    for (const f of readdirSync(dir).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(dir, f), "utf8"));
    db.prepare("INSERT INTO settings (key, value, updated_at) VALUES ('shadow_ledger_kr', ?, 't')").run(집계("2026-11-02"));
    const rows = db.prepare(SHADOW_READ).all() as Array<{ key: string; value: string }>;
    expect(shadowLedgers(rows)[0].asOf).toBe("2026-11-02");
  });
  it("집계가 없으면 첫 집계 때를, 있으면 기준일·종목 수·비용 뒤 평균을 보인다", () => {
    expect(renderToStaticMarkup(createElement(ShadowCard, { ledgers: [] }))).toContain("첫 집계는 2026-11-03(화) 06:07 KST");
    expect(renderToStaticMarkup(createElement(ShadowCard, { ledgers: null }))).toContain("읽지 못했습니다");
    const html = renderToStaticMarkup(createElement(ShadowCard, { ledgers: shadowLedgers([{ key: "shadow_ledger_kr", value: 집계("2026-11-02") }]) }));
    expect(html).toContain("국내");
    expect(html).toContain("2026-11-02 기준");
    expect(html).toContain("2(2)");
    expect(html).toContain("+0.50%");
    expect(html).toContain("50%");
  });
});
