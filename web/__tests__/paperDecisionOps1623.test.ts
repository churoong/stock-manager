/**
 * 운영 사정으로 못 산 줄(매수 단계 건너뜀, 25.1622)은 "잘 거름·놓침" 색을 칠하지 않는다 (docs/infra.md 25.1623)
 */
import { describe, expect, it } from "vitest";
import { holdSide, operationalSkip } from "../lib/paperDecisions";

describe("운영 사정 skip", () => {
  it("당일 단타가 늦은 실행에 진입하지 않은 줄도 운영 사정(25.1886)", () => {
    expect(operationalSkip({ kind: "skip", reason: "단타 진입 없음: 장 열린 뒤 실행" })).toBe(true);
    expect(operationalSkip({ kind: "buy", reason: "단타 진입 없음: 장 열린 뒤 실행" })).toBe(false);
  });
  it("매수 단계 건너뜀 skip 만", () => {
    expect(
      operationalSkip({
        kind: "skip",
        reason: "매수 단계 건너뜀: 미체결 달러 주문의 환율 모름",
      }),
    ).toBe(true);
    expect(
      operationalSkip({ kind: "skip", reason: "투자 여력 부족 남은 50,000원" }),
    ).toBe(false);
    expect(
      operationalSkip({ kind: "buy", reason: "매수 단계 건너뜀: x" }),
    ).toBe(false);
    expect(
      holdSide({ kind: "skip", reason: "매수 단계 건너뜀: 장부 현금이 음수" }),
    ).toBe(false);
  });
});
