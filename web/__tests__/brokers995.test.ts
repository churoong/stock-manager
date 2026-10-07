import { describe, expect, it } from "vitest";
import { BROKER_MIN_N, brokerLine } from "@/lib/brokers";

/** 증권사 성적 한 줄 (docs/brokers.md, 25.995) — 배치 `broker_stats.line` 과 같은 글 */
describe("증권사 성적 한 줄", () => {
  const 성적 = { broker: "가", n_fwd: 30, avg_excess_pct: 1.24, hit_pct: 54.2, n_touch: 10, touch_pct: 30, avg_upside_pct: 38.4, as_of: "2026-10-02" };

  it("배치와 같은 문장", () => {
    expect(brokerLine(성적)).toBe("60거래일 초과수익 평균 +1.2%p · 맞힘 54% (30건) · 목표가 터치 30% (10건) · 제시 상승여력 평균 +38%");
  });

  it("표본이 적으면 성적을 말하지 않는다", () => {
    expect(brokerLine({ ...성적, n_fwd: BROKER_MIN_N - 1 })).toContain("표본 부족");
    expect(brokerLine(undefined)).toContain("성적 없음");
  });
});
