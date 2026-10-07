/** 계좌 전체 노출 문장 (docs/portfolio.md 8장, docs/infra.md 25.1002) — 배치 값을 글로만 바꾼다 */
import { describe, expect, it } from "vitest";
import { lookthroughLines } from "@/lib/portfolio";

describe("ETF 투시", () => {
  it("직접·ETF 몫을 나눠 적고 모르는 ETF 를 밝힌다", () => {
    const lines = lookthroughLines({
      etf_pct: 60, covered_pct: 50, unknown_etfs: ["옛 ETF"], as_of: "2026-10-06",
      by_stock: [
        { stock_id: 1, name: "삼성전자", direct_pct: 40, via_pct: 18, total_pct: 58 },
        { stock_id: 2, name: "SK하이닉스", direct_pct: 0, via_pct: 6, total_pct: 6 },
        { stock_id: 3, name: "현대차", direct_pct: 5, via_pct: 0, total_pct: 5 },
      ],
      by_sector: { 반도체: 64, "ETF 속 미확인": 30 },
    });
    expect(lines[0]).toBe("ETF 를 펼친 실제 노출 — ETF 60.0% 중 구성을 아는 몫 50% (구성 기준 2026-10-06) · 구성을 모르는 ETF: 옛 ETF");
    expect(lines[1]).toBe("종목: 삼성전자 58.0%(직접 40.0 + ETF 18.0) · SK하이닉스 6.0%(ETF) · 현대차 5.0%");
    expect(lines[2]).toBe("업종: 반도체 64.0% · ETF 속 미확인 30.0%");
  });

  it("보유 ETF 가 없으면(배치가 null) 아무것도 적지 않는다", () => {
    expect(lookthroughLines(null)).toEqual([]);
    expect(lookthroughLines(undefined)).toEqual([]);
  });
});
