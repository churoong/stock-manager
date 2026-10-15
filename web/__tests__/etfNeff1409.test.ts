/** 우리 종목 집중 카드의 실효 보유 종목 수 (docs/infra.md 25.1409, docs/factors.md 12.29 P5) — 숫자만, 순위와 무관 */
import { describe, expect, it } from "vitest";
import { effectiveCountText, tiltLine, type Tilt } from "@/lib/etfTilt";

const 기본 = {
  avg_score: 61.4, coverage_pct: 88.2, matched_pct: 99.3, total_pct: 100.1, rec_pct: 3.2, top_pct: 42.3, holdings: 30,
  top: [], vs_market: 9.0, benchmark: "VTI", rank: null, rank_size: null, source_symbol: "SOXX", proxy: false,
  accession: "a", filed: "2026-08-28", report_date: "2026-06-30", score_as_of: "2026-10-02", signals_as_of: "2026-10-02",
  coverage_min_pct: 70,
} as Tilt;

describe("실효 보유 종목 수", () => {
  it("값이 있으면 기준 ETF 와 나란히 숫자만", () => {
    const t = { ...기본, n_eff: 18.4, bench_n_eff: 214.7 };
    expect(effectiveCountText(t)).toBe(" · 실효 보유 종목 약 18 (VTI 약 215)");
    expect(tiltLine(t).endsWith(" · 실효 보유 종목 약 18 (VTI 약 215)")).toBe(true);
    expect(tiltLine(t)).not.toMatch(/분산 부족|집중됨/);
  });
  it("모르면(잘린 목록·옛 행) 적지 않는다", () => {
    expect(effectiveCountText({ ...기본, n_eff: null })).toBe("");
    expect(effectiveCountText(기본)).toBe("");
    expect(effectiveCountText({ ...기본, n_eff: 40.2, bench_n_eff: null })).toBe(" · 실효 보유 종목 약 40");
  });
});
