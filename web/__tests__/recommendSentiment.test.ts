import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { sentimentLine } from "@/lib/recommend";

describe("추천 카드에 센티먼트를 따로 보인다 (docs/infra.md 25.379)", () => {
  it("값과 실제 반영 가중치", () => {
    expect(
      sentimentLine({ sentiment_score: 60.4, sentiment_weight_used: 10 }),
    ).toBe("센티먼트 +60 (종합 점수에 10% 반영)");
    expect(
      sentimentLine({ sentiment_score: -12, sentiment_weight_used: 0 }),
    ).toBe("센티먼트 -12 (종합 점수에 반영 안 됨)");
    expect(
      sentimentLine({ sentiment_score: null, sentiment_weight_used: 0 }),
    ).toBe("센티먼트 반영 안 됨 (값이 없었거나 가중치 0)");
  });

  it("질의가 읽고 카드가 그린다", () => {
    expect(readFileSync("lib/recommend.ts", "utf8")).toContain(
      "sc.sentiment_score, sc.sentiment_weight_used",
    );
    expect(readFileSync("components/RecommendList.tsx", "utf8")).toContain(
      "{sentimentLine(row)}",
    );
  });
});
