/**
 * 체결을 견줄 주문(n)이 0 인 날 — 모름·거부·정리 주문이 있으면 "끝난 주문 없음" 으로 가리지 않는다
 * (docs/paper.md 15.8, docs/infra.md 25.1617, KIS 웹 감사 [필수])
 */
import { describe, expect, it } from "vitest";
import { qualityLine } from "../lib/kisQuality";
import { brokerLines, type BrokerSummary } from "../lib/paper";

const 빈요약: BrokerSummary = {
  days: 28,
  n: 0,
  match_pct: null,
  both: 0,
  internal_only: 0,
  kis_only: 0,
  neither: 0,
  partial: 0,
  rejected: 0,
  unknown: 0,
  cleanup: 0,
  slip_buy: { n: 0, avg: null },
  slip_sell: { n: 0, avg: null },
  reject_reasons: {},
};

describe("견줄 주문 0 인 날", () => {
  it("실행 품질 줄이 모름·거부 건수를 보인다", () => {
    const 줄 = qualityLine({
      as_of: "2026-10-20",
      n: 0,
      match_pct: null,
      kept_pct: null,
      slip_buy: null,
      slip_sell: null,
      rejected: 2,
      unknown: 4,
    });
    expect(줄).toContain("체결을 견줄 주문 없음");
    expect(줄).toContain("모름 4건");
    expect(줄).toContain("거부 2건");
    expect(
      qualityLine({
        as_of: "2026-10-20",
        n: 0,
        match_pct: null,
        kept_pct: null,
        slip_buy: null,
        slip_sell: null,
        rejected: 0,
      }),
    ).toContain("아직 끝난 베낀 주문이 없습니다");
  });
  it("요약 칸이 모름·정리만 있는 날을 끝난 주문 없음으로 말하지 않는다", () => {
    for (const s of [
      { ...빈요약, unknown: 3 },
      { ...빈요약, cleanup: 1 },
    ]) {
      const 줄 = brokerLines({ as_of: "2026-10-20", diff: {}, summary: s });
      expect(줄[0]).toContain("체결을 견줄 주문은 아직 없습니다");
      expect(줄.join("\n")).not.toContain("아직 끝난 베낀 주문이 없습니다");
    }
  });
});
