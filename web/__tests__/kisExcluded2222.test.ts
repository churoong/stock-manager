// 표본 밖 끝 줄이 이름 붙여 센 까닭(개장 뒤 보낸 시장가·일봉 없음, 25.2220)을 읽는다 — 배치 compare_lines 와 같은 글
// (docs/infra.md 25.2222, 25.2220 교차검증 [권고]). 배치 쪽은 tests/test_excluded_lines_2222.py 가 같은 글자를 본다
import { describe, expect, it } from "vitest";
import { brokerLines, EXCLUDED_WHY, excludedDetail, type BrokerSummary } from "@/lib/paper";

const 요약 = (더: Partial<BrokerSummary>): BrokerSummary => ({
  days: 28, n: 0, match_pct: null, both: 0, internal_only: 0, kis_only: 0, neither: 0, partial: 0, rejected: 0, unknown: 0,
  cleanup: 0, slip_buy: { n: 0, avg: null }, slip_sell: { n: 0, avg: null }, reject_reasons: {}, ...더,
});

describe("표본 밖 끝 줄 (25.2222)", () => {
  it("까닭 목록에 일봉 없음이 들고, 센 것이 있으면 수를 괄호 안에 붙인다", () => {
    expect(EXCLUDED_WHY).toBe("가상의 판정 아닌 만료·시세·환율 없어 미룬 매도·장 열린 뒤 시장가·일봉 없음");
    const 줄 = brokerLines({ as_of: "2026-11-09", diff: {}, summary: 요약({ excluded: 5, excluded_late_send: 2, excluded_no_bar: 1 }) });
    expect(줄.at(-1)).toBe(
      "표본 밖 5(가상의 판정 아닌 만료·시세·환율 없어 미룬 매도·장 열린 뒤 시장가·일봉 없음 — 개장 뒤 보낸 국내 시장가 2 · 일봉 없음 1)"
      + " · 잔고가 어긋난 종목 0 (2026-11-09 기준)",
    );
  });
  it("하나만 있으면 그것만, 없으면(0·빠짐) 덧붙이지 않는다", () => {
    expect(excludedDetail(요약({ excluded: 1, excluded_no_bar: 1 }))).toBe(`${EXCLUDED_WHY} — 일봉 없음 1`);
    expect(excludedDetail(요약({ excluded: 3, excluded_late_send: 3 }))).toBe(`${EXCLUDED_WHY} — 개장 뒤 보낸 국내 시장가 3`);
    expect(excludedDetail(요약({ excluded: 2, excluded_late_send: 0 }))).toBe(EXCLUDED_WHY);
  });
});
