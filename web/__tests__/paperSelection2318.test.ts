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
      selection: "made_up_v9",
    });
    expect(글).toContain("모르는 고르기(made_up_v9) — 배치가 건너뜀");
    expect(글).not.toContain("점수 상위");
  });
  it("헌장이 있어도 모르는 값이 먼저다(배치는 헌장 계좌도 건너뛴다)", () => {
    const 글 = ruleText({
      sell_rule: "value",
      top_n: 5,
      selection: "made_up_v8",
      charter_json: JSON.stringify({
        name: "가치투자사",
        agenda: 20,
        top_n: 5,
      }),
    });
    expect(글).toContain("모르는 고르기(made_up_v8)");
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
  it("아는 값은 일곱 — Z3·Z4 단독 고르기 글(25.2319)", () => {
    expect([...PAPER_SELECTIONS].sort()).toEqual([
      "canslim",
      "etf_tactical",
      "magic_formula",
      "random",
      "short",
      "signal",
      "weighted",
    ]);
    expect(
      ruleText({
        sell_rule: "hold_1y",
        entry_rule: "open",
        top_n: 12,
        selection: "magic_formula",
      }),
    ).toBe(
      "매수: 그린블랫 마법공식 — 매월 첫 거래일 유니버스 EY·ROC 순위 상위에서 안 든 1종목, 평가 총액 1/24, 시장당 12종목 · 다음 장 시가에 한 번에 — 매도: 첫 체결 365일 뒤 첫 결정일에 다음 장 시가(손절·목표 없음, 매도 플래그 무시)",
    );
    expect(
      ruleText({
        sell_rule: "canslim_exit",
        entry_rule: "open",
        top_n: 5,
        selection: "canslim",
      }),
    ).toContain("−8% 이하·+25% 이상");
  });
});
