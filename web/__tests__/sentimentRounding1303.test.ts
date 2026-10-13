// 감성 정수 반올림은 텔레그램(`rounding.fixed`, toFixed 규칙)과 같다 — .5 는 절댓값 쪽 (docs/infra.md 25.1303)
import { describe, expect, it } from "vitest";
import { signedInt } from "@/lib/portfolio";

describe("signedInt", () => {
  it(".5 는 절댓값이 큰 쪽", () => {
    expect([-12.5, -41.5, 12.5, -0.3, 0.4].map(signedInt)).toEqual(["-13", "-42", "+13", "0", "0"]);
  });
});

describe("sentimentLine", () => {
  it("추천 카드도 같은 규칙", async () => {
    const { sentimentLine } = await import("@/lib/recommend");
    expect(sentimentLine({ sentiment_score: -12.5, sentiment_weight_used: 10 })).toContain("센티먼트 -13");
    expect(sentimentLine({ sentiment_score: -0.2, sentiment_weight_used: 0 })).toContain("센티먼트 0 ");
  });
});

describe("기사 색 문턱", () => {
  it("배치가 세는 문턱과 같이 경계를 포함한다", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync(new URL("../components/StockDetail.tsx", import.meta.url), "utf8");
    expect(src).toContain('a.score >= 0.05 ? "text-red-600" : a.score <= -0.05 ? "text-blue-600"');
  });
});
