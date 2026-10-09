/** 한 줄 요약 카드 (docs/analysis.md 39장, docs/infra.md 25.1062) */
import { describe, expect, it } from "vitest";
import { summaryCells, type Verdict } from "@/lib/analysis";

const 상세 = (x: Partial<NonNullable<Verdict["detail"]>>) => x as Verdict["detail"];

describe("한 줄 요약", () => {
  it("다섯 칸은 각 장의 사실을 옮긴다 — 밴드 20·80 선, 부호, 가속·감속, Z″ 구간", () => {
    const c = summaryCells(상세({
      outlook: { lines: [], close: 100, close_date: "d", band: { rank: 15, pbr: 0.7, prices: { p20: 1, p50: 2, p80: 3 }, price_date: null },
        momentum: { momentum_3m: -0.12 }, flows: { latest: "d", windows: { "20": { days: 20, frgn: 30, orgn: -10, prsn: -20 } }, frgn_streak: 0, lines: [] } },
      quarters: { basis: "연결", trend: "감속", rows: [] },
      health: { fiscal_year: 2025, consolidated: true, report_date: null, z: 0.8, zone: "distress" },
    }));
    expect(c.map((x) => [x.key, x.tone])).toEqual([["value", "good"], ["trend", "bad"], ["flow", "good"], ["earnings", "bad"], ["risk", "bad"]]);
    expect(c[0].text).toBe("싼 쪽 (밴드 15%)");
    expect(c[2].text).toContain("+20억");
  });
  it("재료가 없으면 회색 '없음' — 지어내지 않는다", () => {
    const c = summaryCells(상세({ outlook: { lines: [], close: 1, close_date: null, band: { rank: 80, pbr: 1, prices: { p20: 1, p50: 1, p80: 1 }, price_date: null } } }));
    expect(c[0]).toMatchObject({ tone: "bad", text: "비싼 쪽 (밴드 80%)" });
    expect(c.slice(1).every((x) => x.tone === "none")).toBe(true);
    expect(summaryCells(null).every((x) => x.tone === "none")).toBe(true);
  });
});
