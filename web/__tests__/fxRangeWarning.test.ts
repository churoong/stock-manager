import { describe, expect, it } from "vitest";
import { fxRangeWarning } from "@/lib/portfolio";

/** 손으로 넣은 원·달러 환율의 오타를 저장 자리에서 말하는가 (docs/infra.md 25.296). */
describe("fxRangeWarning", () => {
  it("자릿수를 빠뜨린 환율은 경고한다", () => {
    expect(fxRangeWarning("USD", "manual", 13.9)).toContain("보통 범위");
    expect(fxRangeWarning("USD", "manual", 13_900)).toContain("보통 범위");
  });

  it("정상 범위·자동 환율·원화는 말하지 않는다", () => {
    expect(fxRangeWarning("USD", "manual", 1_390)).toBeNull();
    expect(fxRangeWarning("USD", "auto", 13.9)).toBeNull();
    expect(fxRangeWarning("KRW", "none", 1)).toBeNull();
  });
});
