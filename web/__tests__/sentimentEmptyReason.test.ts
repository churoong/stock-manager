import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { sentimentEmptyReason } from "@/lib/stockDetail";

describe("점수 칸의 센티먼트가 빈 까닭을 가른다 (docs/infra.md 25.382)", () => {
  it("종합 점수를 안 냈으면 그 까닭", () => {
    expect(
      sentimentEmptyReason({ total_score: null, skip_reason: "팩터 2개 결측" }),
    ).toContain("종합 점수를 내지 않아");
  });
  it("쓴 가중치 0 은 '설정이 0' 과 '값이 없었다' 를 함께 말한다 — 배치가 둘 다 0 으로 저장한다 (25.833)", () => {
    const 글 = sentimentEmptyReason({ total_score: 70, sentiment_weight_used: 0 });
    expect(글).toContain("감성 값이 없었거나");
    expect(글).toContain("가중치가 0");
    // 배치가 실제로 그렇게 저장하는지 — 값이 없으면 0.0
    const 배치 = readFileSync("../batch/services/scoring.py", "utf8");
    expect(배치).toMatch(/use_sentiment = sentiment is not None and sentiment_weight > 0\n    if not use_sentiment:[\s\S]{0,200}sentiment_weight_used=0\.0/);
  });
  it("화면이 박힌 문장 대신 이것을 쓴다", () => {
    expect(readFileSync("components/StockDetail.tsx", "utf8")).toContain(
      "{sentimentEmptyReason(score)}",
    );
  });
});
