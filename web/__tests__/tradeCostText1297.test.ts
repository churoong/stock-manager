// 매도 세금 칸 안내와 매매 목록의 수수료·세금 줄 (docs/infra.md 25.1297, 손익 감사 #5)
import { describe, expect, it } from "vitest";
import { costText, taxFieldLabel } from "@/lib/portfolio";

describe("taxFieldLabel", () => {
  it("미국 매도·국내 ETF 매도는 비우면 0", () => {
    expect(taxFieldLabel("USD", "stock")).toContain("비우면 0");
    expect(taxFieldLabel("KRW", "etf")).toContain("비우면 0");
  });
  it("국내 주식 매도는 설정 비율", () => {
    expect(taxFieldLabel("KRW", "stock")).toContain("설정 비율");
    expect(taxFieldLabel(undefined, undefined)).toContain("설정 비율");
  });
});

describe("costText", () => {
  it("저장된 값을 보여 준다", () => {
    expect(costText({ side: "sell", currency: "KRW", asset_type: "stock", fee: 150, tax: 2000 })).toBe("수수료 150원 · 세금 2,000원");
  });
  it("빈 칸은 추정이라 적고, 세금 없는 매도는 0", () => {
    expect(costText({ side: "buy", currency: "KRW", fee: null, tax: null })).toBe("수수료 설정 비율로 추정");
    expect(costText({ side: "sell", currency: "KRW", asset_type: "stock", fee: 10, tax: null })).toContain("세금 설정 세율로 추정");
    expect(costText({ side: "sell", currency: "USD", fee: 1, tax: null })).toContain("세금 $0.00");
    expect(costText({ side: "sell", currency: "KRW", asset_type: "etf", fee: 1, tax: null })).toContain("세금 0원");
  });
});

describe("배선", () => {
  it("고치기 창도 입력 칸과 같은 세금 안내를 쓴다", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync(new URL("../components/PortfolioView.tsx", import.meta.url), "utf8");
    expect(src).toContain("prompt(taxFieldLabel(t.currency, t.asset_type)");
    expect(src).not.toContain("비우면 설정 세율)`");
  });
});
