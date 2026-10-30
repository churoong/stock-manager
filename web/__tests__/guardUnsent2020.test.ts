// 25.2020 (25.2017 교차검증 [권고]) — 보내지 않은 장중 줄은 웹 KIS 요약에도 따로 한 줄(배치 compare_lines 와 같은 글)
import { describe, expect, it } from "vitest";
import { brokerLines, type BrokerSummary } from "../lib/paper";

const 빈: BrokerSummary = {
  days: 28, n: 0, match_pct: null, both: 0, internal_only: 0, kis_only: 0, neither: 0, partial: 0, rejected: 0, unknown: 0,
  cleanup: 0, slip_buy: { n: 0, avg: null }, slip_sell: { n: 0, avg: null }, reject_reasons: {},
};

describe("brokerLines guard_unsent", () => {
  it("보내지 않은 장중 줄만 있어도 따로 보인다", () => {
    const 줄 = brokerLines({ as_of: "2026-10-30", diff: {}, summary: { ...빈, guard_unsent: 1 } });
    expect(줄[0]).toContain("체결을 견줄 주문은 아직 없습니다");
    expect(줄.at(-1)).toContain("장중 손절·익절 못 냄 1(우리 기록 확인 실패 — KIS 에 안 감)");
    expect(줄.at(-1)).not.toContain("장중 손절·익절 1(");
  });
});
