// 스윙 계좌 S 의 화면 글 (docs/paper.md 16.3.1, docs/infra.md 25.1872) — 계좌 행은 0092(2026-10-29 개설), 글은 먼저 들어갔다
import { describe, expect, it } from "vitest";

import { ruleText } from "@/lib/paper";

describe("스윙 S 규칙 글", () => {
  it("단기 신호만·든 종목은 더 사지 않음·종가 판정 매도를 말한다", () => {
    const 글 = ruleText({ sell_rule: "swing_close", entry_rule: "open", top_n: 10, selection: "short" });
    expect(글).toContain("단기 신호만 점수 상위 10종목(든 종목은 더 사지 않음)");
    expect(글).toContain("다음 장 시가에 한 번에");
    expect(글).toContain("−3% 이하·+5% 이상이거나 10거래일째면 다음 장 시가");
    expect(글).not.toContain("swing_close"); // 날 값이 그대로 보이지 않는다
  });
});
