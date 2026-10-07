import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { money } from "@/lib/portfolio";

describe("종목 상세 배당의 주당배당금은 종목 통화로 (docs/infra.md 25.381)", () => {
  it("미국은 달러, 국내는 원", () => {
    expect(money(0.96, "USD")).toBe("$0.96");
    expect(money(1444, "KRW")).toContain("원");
  });

  it("화면이 '원' 을 박지 않고, 빈 칸 사유가 미국을 안 받는다고 하지 않는다", () => {
    expect(readFileSync("components/StockDetail.tsx", "utf8")).not.toContain(
      'r.dps_common.toLocaleString("ko-KR")}원',
    );
    expect(readFileSync("lib/stockDetail.ts", "utf8")).not.toContain(
      "미국은 아직 수집하지 않습니다",
    );
  });
});
