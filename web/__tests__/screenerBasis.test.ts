import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { screenerBasisLine, type ScreenerRow } from "@/lib/screener";

const 행 = (값: Partial<ScreenerRow>) =>
  ({ price_date: null, fiscal_year: null, ...값 }) as ScreenerRow;

describe("스크리너 결과에 데이터 기준 시각을 적는다 (docs/infra.md 25.364)", () => {
  it("스냅샷·종가 범위·성과 계산일·주된 사업연도", () => {
    const 줄 = screenerBasisLine([
      행({
        snapshot_date: "2026-09-21",
        metrics_asof: "2026-09-25",
        price_date: "2026-09-25",
        fiscal_year: 2025,
      }),
      행({
        snapshot_date: "2026-09-21",
        metrics_asof: "2026-09-25",
        price_date: "2026-09-24",
        fiscal_year: 2025,
      }),
      행({
        snapshot_date: "2026-09-21",
        metrics_asof: "2026-09-25",
        price_date: "2026-09-25",
        fiscal_year: 2024,
      }),
    ]);
    expect(줄).toBe(
      "기준: 시총·거래대금 2026-09-21 스냅샷 · 종가 2026-09-24~2026-09-25 · 성과 지표 2026-09-25 계산 · 재무 주로 2025 사업보고서",
    );
  });

  it("결과가 없으면 줄도 없다", () => {
    expect(screenerBasisLine([])).toBeNull();
  });

  it("질의가 기준일을 읽고 경로와 화면이 그 줄을 싣는다", () => {
    expect(readFileSync("lib/screener.ts", "utf8")).toContain(
      "(SELECT d FROM snapshot) AS snapshot_date",
    );
    expect(readFileSync("app/api/screener/route.ts", "utf8")).toContain(
      "basis: screenerBasisLine(rows)",
    );
    expect(readFileSync("components/ScreenerForm.tsx", "utf8")).toContain(
      "setBasis(body.basis ?? null)",
    );
  });
});

describe("비율 없는 종목 수 (docs/infra.md 25.491)", () => {
  it("유니버스 밖처럼 점수가 없는 종목은 비율이 비고 그 수를 말한다", async () => {
    const { screenerBasisLine } = await import("@/lib/screener");
    const 행 = (per: number | null) => ({ per, pbr: null, roe: null, debt_ratio: null, operating_margin: null,
      factors_asof: "2026-09-25", snapshot_date: null, price_date: null, metrics_asof: null, fiscal_year: null });
    const 줄 = screenerBasisLine([행(10), 행(null), 행(null)] as never);
    expect(줄).toContain("비율 없는 2종목");
  });
});

describe("비율 조건이 비율 없는 종목을 뺀다 (docs/infra.md 25.497)", () => {
  it("유니버스를 끄고 비율 조건을 걸면 말한다", async () => {
    const { ratioFilterNote, defaultFilters } = await import("@/lib/screener");
    const 끔 = { ...defaultFilters("KR"), universe_only: false };
    expect(ratioFilterNote({ ...끔, per: { min: null, max: 10 } })).toContain("PER 조건을 걸면, 값이 없는 종목");
    expect(ratioFilterNote({ ...끔, roe: { min: 5, max: null }, pbr: { min: null, max: 1 } })).toContain("PBR·ROE");
    expect(ratioFilterNote(끔)).toBeNull();
    // 유니버스 안에서도 PER 은 적자면 빈다 (25.523, 교차검증)
    expect(ratioFilterNote({ ...끔, universe_only: true, per: { min: null, max: 10 } })).toContain("PER(적자)");
    // PBR 도 자본잠식이면 빈다 (25.540, 교차검증 — 전에는 이 경우를 null 로 고정했다)
    expect(ratioFilterNote({ ...끔, universe_only: true, pbr: { min: null, max: 1 } })).toContain("PBR(자본잠식)");
    expect(ratioFilterNote({ ...끔, universe_only: true, market_cap: { min: 1000, max: null } })).toBeNull();
    // 유니버스 안에서도 성장률은 빈다 — 전년 적자·흑자 전환 (25.511, 교차검증)
    expect(ratioFilterNote({ ...끔, universe_only: true, operating_income_growth: { min: 0, max: null } }))
      .toContain("전년 값이 0 이하이거나 없음");
    expect(ratioFilterNote({ ...끔, cagr: { min: 5, max: null } })).toBeNull(); // 성과 지표는 점수와 무관
    // 성장률도 팩터 표에서 온다 (25.500, 교차검증)
    expect(ratioFilterNote({ ...끔, revenue_growth: { min: 10, max: null } })).toContain("매출성장률");
    expect(ratioFilterNote({ ...끔, operating_income_growth: { min: null, max: 50 } })).toContain("영업이익성장률");
  });

  it("API 가 안내에 싣는다", async () => {
    const { readFileSync } = await import("node:fs");
    expect(readFileSync("app/api/screener/route.ts", "utf8")).toContain("ratioFilterNote(filters)");
  });
});

describe("감성 기준일 (docs/infra.md 25.499)", () => {
  it("폰에서도 보이게 기준 줄에 범위로 싣는다", async () => {
    const { screenerBasisLine } = await import("@/lib/screener");
    const 행 = (d: string | null, sentiment: number | null = 5) => ({ sentiment_date: d, sentiment, per: 1, pbr: 1, roe: 1, debt_ratio: 1, operating_margin: 1,
      factors_asof: null, snapshot_date: null, price_date: null, metrics_asof: null, fiscal_year: null });
    expect(screenerBasisLine([행("2026-09-25"), 행(null), 행("2026-09-20")] as never)).toContain("감성 2026-09-20~2026-09-25");
    expect(screenerBasisLine([행("2026-09-25")] as never)).toContain("감성 2026-09-25");
    expect(screenerBasisLine([행(null)] as never)).toBeNull();
    // 점수가 빈 행(기사 5건 미만)의 날짜는 모으지 않는다 (25.502, 교차검증)
    expect(screenerBasisLine([행("2026-09-27", null), 행("2026-05-01", 12)] as never)).toBe("기준: 감성 2026-05-01");
    expect(screenerBasisLine([행("2026-09-27", null)] as never)).toBeNull();
  });
});
