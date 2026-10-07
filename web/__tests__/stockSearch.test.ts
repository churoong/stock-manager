/**
 * 메뉴 종목 검색 규칙 (components/StockSearch).
 */

import { describe, expect, it } from "vitest";
import { resultLabel, searchUrl } from "@/lib/stockSearch";

describe("검색 주소", () => {
  it("빈 칸이면 묻지 않는다", () => {
    expect(searchUrl("")).toBeNull();
    expect(searchUrl("   ")).toBeNull();
  });

  it("한글을 주소에 맞게 바꾸고 40자로 자른다", () => {
    expect(searchUrl(" 삼성전자 ")).toBe(`/api/stocks/search?q=${encodeURIComponent("삼성전자")}`);
    const long = "가".repeat(50);
    expect(searchUrl(long)).toBe(`/api/stocks/search?q=${encodeURIComponent("가".repeat(40))}`);
  });
});

describe("결과 한 줄", () => {
  const base = { id: 1, country: "KR", currency: "KRW" };
  it("이름 · 코드 · 시장", () => {
    expect(resultLabel({ ...base, name: "삼성전자", ticker: "005930", market: "KOSPI" })).toBe("삼성전자 005930 · KOSPI");
  });

  it("이름이 코드와 같으면 한 번만", () => {
    expect(resultLabel({ ...base, name: "AAPL", ticker: "AAPL", market: "NASDAQ" })).toBe("AAPL · NASDAQ");
  });
});
