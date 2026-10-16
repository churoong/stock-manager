/**
 * 결정 단위 선정 증거 — 관문 칸·챔피언 글 (docs/paper.md 10.1, docs/desk.md 6장, docs/infra.md 25.1518).
 */

import { describe, expect, it } from "vitest";
import { championText, evidenceText, gateNowText } from "@/lib/desk";

describe("결정 단위 선정 증거", () => {
  it("관문 칸 글", () => {
    expect(gateNowText({ key: "selection", label: "", now: 52.34, target: 50, ok: true, n: 7, mean: 61.2 })).toBe(
      "하한 52.3분위(평균 61.2, 28일 묶음 7) / 50 초과",
    );
    expect(gateNowText({ key: "selection", label: "", now: null, target: 50, ok: false, n: 2 })).toBe("모름");
    expect(gateNowText({ key: "vs_index", label: "", now: -3.5, target: 0, ok: false, index_pct: 8 })).toBe("-3.50%p (지수 반반 +8.00%) / 0 초과");
  });

  it("챔피언 줄 — 짝 차이·근거 부족·옛 요약", () => {
    const r = { id: "C", from: "2026-01-01", blocks: 3, wins: 3, pct: 12, champion_pct: 6, candidate: true, blocks_ok: true };
    expect(evidenceText({ ...r, evidence_diff: { n_blocks: 5, mean: 1.2, lower: -0.4, alpha: 0.01 } })).toBe(" · 결정 단위 +1.20%p(하한 -0.40, 묶음 5)");
    expect(evidenceText({ ...r, evidence_diff: { n_blocks: 2, alpha: 0.01 } })).toBe(" · 결정 단위 근거 부족(묶음 2)");
    expect(championText(r, "H")).toBe("C · 블록 3개 중 3개 이김 · 같은 기간 +12.00% (H +6.00%) · 2026-01-01부터");
  });
});
