import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { EMPTY_DESK, pickDesk } from "@/lib/desk";

/** 위험판 화면 이음매 (docs/infra.md 25.2283 — 25.2282 교차검증 [권고]: 셀·설명 문단을 지워도 깨지는 시험이 없었다) */
describe("위험판 화면", () => {
  const 화면 = readFileSync(
    join(process.cwd(), "components", "DeskView.tsx"),
    "utf8",
  );

  it("열린 위험·지수 충격 셀을 그린다", () => {
    expect(화면).toContain("{openRiskText(r)}</td>");
    expect(화면).toContain("{indexShockText(r)}</td>");
    // 머리칸은 설명 문단 **앞** 조각에서 찾는다 — 문단에도 같은 글이 있어 머리칸만 지워도 통과했다(25.2285)
    const 표 = 화면.slice(0, 화면.indexOf("노출 = 보유 평가 ÷ 평가 총액"));
    expect(표).toContain("지수 −{DESK_RULES.RISK_INDEX_SHOCK_PCT}%");
    expect(표).toMatch(/열린 위험\s*<\/th>/);
  });

  it("읽는 법이 표 아래 문단에 있다 — 터치 화면은 머리칸 title 이 뜨지 않는다", () => {
    const 문단 = 화면.slice(
      화면.indexOf("노출 = 보유 평가 ÷ 평가 총액"),
      화면.indexOf(
        "KIS모의투자 계좌",
        화면.indexOf("노출 = 보유 평가 ÷ 평가 총액"),
      ),
    );
    for (const 글 of [
      "열린 위험 = ",
      "아래쪽 한계",
      "지수 −{DESK_RULES.RISK_INDEX_SHOCK_PCT}% = ",
      "선형 어림",
      "급락 날은 더 클 수 있다",
      "베타를 모르면 1 로",
      "ETF 전술 계좌는 내지 않는다",
    ]) {
      expect(문단, 글).toContain(글);
    }
    expect(문단).toContain("{desk.risk_beta_error ? (");
    expect(문단).toContain("— {desk.risk_beta_error}</p>");
    expect(문단).toContain("지수 충격 칸을 내지 못함(베타 읽기 실패)");
  });

  it("베타를 못 읽은 까닭을 요약에서 꺼낸다 — 예전 요약에는 없다(null)", () => {
    expect(EMPTY_DESK.risk_beta_error).toBeNull();
    const 줄 = (s: object) => [
      {
        as_of: "2026-11-24",
        updated_at: "t",
        summary_json: JSON.stringify({
          leaderboard: { rows: [] },
          markets: {},
          ...s,
        }),
      },
    ];
    expect(
      pickDesk(
        줄({ risk_beta_error: "RuntimeError: no such function: json_each" }),
      ).risk_beta_error,
    ).toBe("RuntimeError: no such function: json_each");
    expect(pickDesk(줄({})).risk_beta_error).toBeNull();
  });
});
