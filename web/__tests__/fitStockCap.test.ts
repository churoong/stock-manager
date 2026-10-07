import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fitStockCap } from "@/lib/settings";

describe("종목 상한이 섹터 상한보다 크면 배치처럼 줄인다 (docs/infra.md 25.357)", () => {
  it("40 / 30 이면 30 으로 쓰고 말한다", () => {
    const r = fitStockCap(40, 30);
    expect(r.stock).toBe(30);
    expect(r.warning).toContain("30%");
  });

  it("앞뒤가 맞으면 그대로", () => {
    expect(fitStockCap(10, 30)).toEqual({ stock: 10, warning: null });
  });

  it("적립 종목 경로가 줄이고 경고를 돌려주며, 화면이 경고를 그린다", () => {
    expect(readFileSync("app/api/etf/stocks/route.ts", "utf8")).toContain(
      "fitStockCap(종목.value, 섹터.value)",
    );
    expect(readFileSync("components/AccumulationStocks.tsx", "utf8")).toContain(
      "...(caps?.warnings ?? [])",
    );
  });
});
