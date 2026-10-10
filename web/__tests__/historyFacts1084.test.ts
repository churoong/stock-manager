/** 배치가 내는데 화면에 안 나오던 이력 값 (docs/infra.md 25.1084) — 서버 렌더로 글자를 본다 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import AnalysisExtras from "@/components/AnalysisExtras";
import HistoryFacts from "@/components/HistoryFacts";
import { HISTORY_LINE_PREFIXES, type HistoryData } from "@/lib/analysis";

describe("이력 카드가 진단 줄 대신 그리는 값", () => {
  it("실적 발표 반응 요약(늘어난·줄어든 발표)을 그린다 — 진단 줄은 목록에서 빠지므로 여기 없으면 사라진다", () => {
    expect(HISTORY_LINE_PREFIXES).toContain("실적 발표 반응(");
    const h = { earnings: { basis: "연결", events: 12, recent: [], grew: { n: 7, avg: 0.021, up: 5 }, shrank: { n: 5, avg: -0.013, up: 1 } } } as HistoryData;
    const html = renderToStaticMarkup(createElement(HistoryFacts, { h }));
    expect(html).toContain("영업이익이 늘어난 발표 7번");
    expect(html).toContain("+2.1%");
    expect(html).toContain("줄어든 발표 5번");
  });

  it("가장 깊었던 낙폭을 그린다 (31장)", () => {
    const h = {
      drawdown: {
        since: "2021-01-04", until: "2026-10-08", now: -0.1, peak_date: "2026-07-01", days_since_peak: 70, levels: {},
        worst: [{ peak: "2021-06-01", trough: "2022-10-12", depth: -0.52, recovered: null, days: null },
          { peak: "2023-03-02", trough: "2023-05-10", depth: -0.21, recovered: "2023-09-01", days: 128 }],
      },
    } as unknown as HistoryData;
    const html = renderToStaticMarkup(createElement(HistoryFacts, { h }));
    expect(html).toContain("가장 깊었던 2번");
    expect(html).toContain("-52.0%");
    expect(html).toContain("아직 회복 못 함");
    expect(html).toContain("128거래일");
  });

  it("함께 움직이는 종목이 비어도 짝 거래 줄은 그린다 (53장)", () => {
    const pairsGap = { gap: 0.034, own: -0.01, peers: 0.02, n: 48, beta: 1.2, until: "2026-10-08", formation_until: "2026-09-08" };
    const html = renderToStaticMarkup(createElement(AnalysisExtras, { comovers: [], pairsGap } as never));
    expect(html).toContain("벌어진 폭 +3.4%p");
  });
});
