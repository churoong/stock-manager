/**
 * 핵심 H 다년 백테스트 글 (docs/backtest.md 10장, docs/infra.md 25.1545) — 배치가 낸 값을 글로만.
 */

import { describe, expect, it } from "vitest";
import { afterSellText, coreLines, coreNotes, quantileText } from "@/lib/coreBacktest";

const m = (o: object) => JSON.stringify(o);

describe("핵심 H 결과 글 (25.1545)", () => {
  it("분위·판 뒤 글", () => {
    expect(quantileText({ n_blocks: 8, mean: 54.24, lower: 51.0 })).toBe("평균 54.2 · 하한 51.0 (묶음 8)");
    expect(quantileText({ n_blocks: 3, mean: 54.24, lower: null })).toBe("평균 54.2 · 하한 - (묶음 3)");
    expect(quantileText({ n_blocks: 0 })).toBe("아직 없음");
    expect(afterSellText({ sells: 12, mean: -1.234, n_blocks: 5 })).toBe("-1.23%p · 매도 12건 (묶음 5)");
    expect(afterSellText({ sells: 0 })).toBe("매도 없음");
  });

  it("변형 순서는 H·G·B·R1·R2, 모르는 이름은 뺀다", () => {
    const rows = ["core_r2", "core_b", "x", "core_h"].map((s) => ({
      strategy: s, excess_cagr: 0.01, metrics_json: m({ cagr: 0.1, mdd: -0.2, evidence: { n_blocks: 7, mean: 55, lower: 52, gate: { n_blocks: 6, mean: 60, lower: 53 }, after_sell: { sells: 3, mean: -0.5, n_blocks: 2 } } }),
    }));
    const l = coreLines(rows);
    expect(l.map((x) => x.strategy)).toEqual(["core_h", "core_b", "core_r2"]);
    expect(l[0].pick).toBe("평균 55.0 · 하한 52.0 (묶음 7)");
    expect(l[0].gate).toBe("평균 60.0 · 하한 53.0 (묶음 6)");
    expect(l[0].cagr).toBe(0.1);
  });

  it("판정·짝·가중 대조 줄 — 기본 실행이 아니면 참고만", () => {
    const step = {
      verdict: "근거 없음", params: { years: 5, risk_every: "week" },
      pairs: { "core_h-core_g": { n_blocks: 9, mean: 0.4, lower: -0.2 } },
      holding_eq: { verdict: "undecidable", differ_days: 12, differ_pct: 1.0, pairs: 10, lower: null },
      timing: { load_s: 300, variants_s: { core_h: 1000, core_g: 900 }, evidence_s: 40 },
    };
    const n = coreNotes(step);
    expect(n[0]).toBe("판정: 근거 없음 (사전 등록 — 첫 국내 기본 실행 하나로만 본다)");
    expect(n[1]).toBe("H−G 짝(합의 선정의 몫, 20 거래일 KOSPI 대비): 평균 +0.40%p · 하한 -0.20%p (묶음 9)");
    expect(n[2]).toContain("가중 대 동일가중: 판정 불가 — 고른 종목이 갈린 날 12일(1.0%)");
    expect(n[3]).toBe("걸린 시간: 읽기 300.0초 · 변형 1900.0초 · 판정 40.0초");
    expect(coreNotes({ ...step, params: { years: 1, risk_every: "week" } })[0]).toContain("기본 실행(5년·리스크 주마다)이 아니라 참고만");
    expect(coreNotes({ holding_eq: { error: "ValueError x" } })).toEqual(["가중 대 동일가중: 진단 오류 — ValueError x"]);
  });
});
