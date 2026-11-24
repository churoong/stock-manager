/** 모르는 종목 고르기 값의 계좌 규칙 글 (25.2318) — 배치는 그 계좌를 건너뛰는데(25.2317) 화면이 "점수 상위 N종목" 으로 떨어지면
 * 사지도 않는 계좌를 산다고 적는다 */
import { describe, expect, it } from "vitest";
import { PAPER_SELECTIONS, ruleText } from "@/lib/paper";

describe("ruleText 모르는 고르기", () => {
  it("모르는 값은 건너뜀으로 적고 점수 상위로 떨어지지 않는다", () => {
    const 글 = ruleText({
      sell_rule: "hold_1y",
      entry_rule: "open",
      top_n: 20,
      selection: "magic_formula",
    });
    expect(글).toContain("모르는 고르기(magic_formula) — 배치가 건너뜀");
    expect(글).not.toContain("점수 상위");
  });
  it("헌장이 있어도 모르는 값이 먼저다(배치는 헌장 계좌도 건너뛴다)", () => {
    const 글 = ruleText({
      sell_rule: "value",
      top_n: 5,
      selection: "canslim",
      charter_json: JSON.stringify({
        name: "가치투자사",
        agenda: 20,
        top_n: 5,
      }),
    });
    expect(글).toContain("모르는 고르기(canslim)");
  });
  it("값이 없거나 signal 이면 그대로 점수 상위", () => {
    expect(ruleText({ sell_rule: "core", top_n: 10 })).toContain(
      "점수 상위 10종목",
    );
    expect(
      ruleText({ sell_rule: "core", top_n: 10, selection: null }),
    ).toContain("점수 상위 10종목");
    expect(
      ruleText({ sell_rule: "core", top_n: 7, selection: "signal" }),
    ).toContain("점수 상위 7종목");
  });
  it("아는 값은 다섯", () => {
    expect([...PAPER_SELECTIONS].sort()).toEqual([
      "etf_tactical",
      "random",
      "short",
      "signal",
      "weighted",
    ]);
  });
});
