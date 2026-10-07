import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { targetPriceProblem } from "@/lib/watch";

describe("관심 종목 목표 매수가의 단위 실수를 막는다 (docs/infra.md 25.360)", () => {
  it("미국 종목에 원화로 넣으면 막는다", () => {
    expect(targetPriceProblem(300000, 230.5, "USD")).toContain("달러");
  });

  it("국내 종목에 달러 같은 작은 값도 막는다", () => {
    expect(targetPriceProblem(50, 70000, "KRW")).toContain("원");
  });

  it("정상 범위와 모르는 종가는 통과", () => {
    expect(targetPriceProblem(200, 230.5, "USD")).toBeNull();
    expect(targetPriceProblem(300000, null, "USD")).toBeNull();
    expect(targetPriceProblem(null, 230.5, "USD")).toBeNull();
  });

  it("추가·수정 두 경로가 모두 검사하고, 목록이 통화를 붙인다", () => {
    expect(readFileSync("app/api/watchlist/route.ts", "utf8")).toContain(
      "targetPriceProblem(",
    );
    expect(readFileSync("app/api/watchlist/[id]/route.ts", "utf8")).toContain(
      "targetPriceProblem(",
    );
    expect(readFileSync("components/AlertCenter.tsx", "utf8")).toContain(
      "money(w.target_buy_price, w.currency)",
    );
  });
});
