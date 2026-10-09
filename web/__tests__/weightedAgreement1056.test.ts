/** 성적 가중 합의 한 줄 (docs/analysis.md 30장, docs/infra.md 25.1056) */
import { describe, expect, it } from "vitest";
import { weightedAgreementLine } from "@/lib/analysis";

describe("성적 가중 합의", () => {
  it("성적이 모자라면 값 대신 언제부터인지 말한다", () => {
    expect(weightedAgreementLine({ value: null, available_from: "2027-10-08", pending: ["capm"] })).toContain("2027-10-08부터");
    expect(weightedAgreementLine(null)).toBeNull();
  });
  it("가중치와 뺀 눈을 함께 적는다", () => {
    const s = weightedAgreementLine({ value: 0.135, weights: { capm: 0.75, consensus: 0.25 }, left_out: ["scenario"] })!;
    expect(s).toContain("+13.5%");
    expect(s).toContain("CAPM(시장·베타) 75%");
    expect(s).toContain("뺀 눈: 시나리오(밴드×순자산)");
  });
});
