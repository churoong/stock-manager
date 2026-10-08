"use client";

import { horizonLabel, type ForecastData } from "@/lib/analysis";
import { formatPrice } from "@/lib/stockDetail";

/**
 * 예상 주가 표 (docs/analysis.md 10장, 25.1025). 종목 화면 맨 위 — 2026-10-08 사용자 "화면 상단에 표로 보여주면 좋겠다".
 * 배치(`verdict.forecast`)가 낸 값을 그리기만 한다. 종가 대비 % 는 표시용 나눗셈이다(투자 수치를 새로 만들지 않는다).
 */
export default function ForecastTable({ f, close, closeDate, currency }: {
  f: ForecastData; close: number | null; closeDate: string | null; currency: string;
}) {
  const 대비 = (x: number) => (close ? `${x >= close ? "+" : ""}${((x / close - 1) * 100).toFixed(1)}%` : "-");
  const 범위 = (lo?: number, hi?: number) => (lo !== undefined && hi !== undefined ? `${formatPrice(lo, currency)} ~ ${formatPrice(hi, currency)}` : "-");
  const pct = (x: number) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
  return (
    <section id="forecast" className="mb-4 rounded-xl border border-violet-200 p-3 dark:border-violet-900">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">예상 주가</h2>
        <span className="text-xs text-slate-500">기준 종가 {formatPrice(close, currency)}{closeDate ? ` (${closeDate})` : ""}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[30rem] text-left text-sm">
          <thead className="text-xs text-slate-500">
            <tr><th className="py-1 pr-2">기간</th><th className="pr-2">예상 주가</th><th className="pr-2">종가 대비</th><th className="pr-2">68% 범위</th><th className="pr-2">90% 범위</th><th className="pr-2">오를 확률</th><th>−20% 이하</th></tr>
          </thead>
          <tbody>
            {f.horizons.map((h) => (
              <tr key={h.months} className="border-t border-slate-100 dark:border-slate-800">
                <td className="py-1.5 pr-2">{horizonLabel(h.months)}</td>
                <td className="pr-2 font-semibold">{formatPrice(h.expected, currency)}</td>
                <td className="pr-2">{대비(h.expected)}</td>
                <td className="pr-2 text-xs">{범위(h.low68, h.high68)}</td>
                <td className="pr-2 text-xs">{범위(h.low90, h.high90)}</td>
                <td className="pr-2">{h.p_up === undefined ? "-" : `${(h.p_up * 100).toFixed(0)}%`}</td>
                <td>{h.p_drop === undefined ? "-" : `${(h.p_drop * 100).toFixed(0)}%`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-slate-500">
        기대 연수익 {pct(f.er)} = 무위험 {pct(f.rf)}{f.rf_given ? "" : "(설정 없음)"} + 베타 {f.beta.toFixed(2)}{f.beta_given ? "" : "(없음)"} × (시장{" "}
        {pct(f.market.annual)} − 무위험) · 시장은 {f.market.index ?? "지수"} {f.market.years.toFixed(1)}년({f.market.since}~{f.market.until}) 연환산
        {f.sigma ? ` · 변동성 ${(f.sigma * 100).toFixed(0)}%` : " · 변동성이 없어 범위를 내지 못했습니다"}
      </p>
      <p className="mt-1 text-xs text-slate-400">
        시장 수익률과 베타(CAPM), 변동성으로 낸 통계적 값입니다 — 점수·신호로 맞힌 값이 아니고, 예상 주가 하나보다 범위가 본론입니다.
      </p>
    </section>
  );
}
