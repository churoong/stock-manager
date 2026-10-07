import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { MASTER } from "@/lib/stockDetail";

// 종목 상세에서 ETF 를 매도 입력하면 세금 칸이 "비우면 설정 비율"(주식 증권거래세)로 안내했다 — 상세가 asset_type 을
// 읽지도 넘기지도 않았다 (docs/infra.md 25.904). 배치는 ETF 의 빈 세금을 0 으로 읽어(25.896) 안내와 계산이 엇갈렸다
describe("종목 상세의 매매 입력이 ETF 를 안다 (25.904)", () => {
  it("상세 질의가 asset_type 을 읽는다", () => {
    expect(MASTER).toMatch(/\basset_type\b/);
  });
  it("상세가 매매 입력에 asset_type 을 넘긴다", () => {
    const 글 = readFileSync("components/StockDetail.tsx", "utf-8");
    const 칸 = 글.slice(글.indexOf("<QuickTrade"), 글.indexOf("/>", 글.indexOf("<QuickTrade")));
    expect(칸).toContain("asset_type:");
  });
});

describe("ETF 상세의 빈 점수 칸 (25.905)", () => {
  it("ETF 는 '아직 계산되지 않았다' 가 아니라 점수를 내지 않는다고 적는다", () => {
    const 글 = readFileSync("components/StockDetail.tsx", "utf-8");
    expect(글).toContain('stock.asset_type === "etf" ? "ETF 는 종목 점수를 내지 않습니다');
  });
});
