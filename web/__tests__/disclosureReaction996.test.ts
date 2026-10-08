import { describe, expect, it } from "vitest";
import { REACTION_MIN_N, classifyTitle, reactionLine, type ReactionRow } from "@/lib/disclosureReaction";

/** 공시 반응 한 줄 (docs/disclosure_reaction.md, 25.996) — 유형 규칙은 배치가 표에 적은 낱말을 그대로 쓴다 */
const 줄 = (type: string, words: string[], priority: number, n = 120): ReactionRow => ({
  type, label: type === "buyback" ? "자기주식 취득" : "유상증자", keywords: JSON.stringify(words), priority, n,
  mean_pct: 1.234, median_pct: 0.81, pos_pct: 56.2, window_days: 5,
});
const 표 = [줄("rights", ["유상증자"], 2), 줄("buyback", ["자기주식취득", "자기주식 취득"], 0)];

describe("공시 반응", () => {
  it("공백을 빼고 우선순위대로 가른다 — 정정 공시는 없음", () => {
    expect(classifyTitle("주요사항보고서(자기주식 취득 결정)", 표)?.type).toBe("buyback");
    expect(classifyTitle("주요사항보고서(유상증자결정)", 표)?.type).toBe("rights");
    expect(classifyTitle("[기재정정]주요사항보고서(유상증자결정)", 표)).toBeNull();
    expect(classifyTitle("임원ㆍ주요주주특정증권등소유상황보고서", 표)).toBeNull();
  });

  it("배치와 같은 문장, 표본이 적으면 없음", () => {
    expect(reactionLine(표[1])).toBe("자기주식 취득 공시: 전날 종가부터 5거래일째까지(공시일 반응 포함) 지수 대비 중앙값 +0.8% · 평균 +1.2% · 오른 비율 56% (120건)");
    expect(reactionLine({ ...표[1], n: REACTION_MIN_N - 1 })).toBeNull();
  });
});
