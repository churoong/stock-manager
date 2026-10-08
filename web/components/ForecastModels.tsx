"use client";

import { horizonLabel, type AnalogData, type ScenarioData } from "@/lib/analysis";
import { formatPrice } from "@/lib/stockDetail";

/**
 * 예상 주가의 다른 두 눈 (docs/analysis.md 13·14장, 25.1039·25.1040) — CAPM 표 아래.
 * 비슷한 국면(그 종목 자신의 과거)과 1년 시나리오(순자산 성장 × 자기 PBR 밴드). 배치가 낸 수익률·가격을 그린다 —
 * 가격 = 종가 × (1 + 수익률) 은 표시용 곱셈이다.
 */
export default function ForecastModels({ analog, scenario, close, currency }: {
  analog?: AnalogData | null; scenario?: ScenarioData | null; close: number | null; currency: string;
}) {
  const pct = (x: number) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
  const px = (r: number) => (close ? formatPrice(close * (1 + r), currency) : pct(r));
  if (!analog && !scenario) return null;
  return (
    <section id="forecast-models" className="mb-4 rounded-xl border border-slate-200 p-3 dark:border-slate-800">
      {analog && (
        <div className="mb-3">
          <h2 className="mb-1 text-sm font-semibold">비슷한 국면 — 이 종목의 과거에서</h2>
          {analog.empty || !analog.horizons?.length ? (
            <p className="text-xs text-slate-500">지금 상태({analog.label})는 이 종목 과거에 드물어 분포를 내지 않습니다.</p>
          ) : (
            <>
              <p className="mb-1 text-xs text-slate-600 dark:text-slate-300">
                지금과 같은 상태 — <b>{analog.label}</b> — 였던 날 {analog.days?.toLocaleString()}일(국면 {analog.episodes}번,{" "}
                {analog.since}~{analog.until})
              </p>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[28rem] text-left text-xs">
                  <thead className="text-slate-500">
                    <tr><th className="py-1 pr-2">기간</th><th className="pr-2">중앙값</th><th className="pr-2">68% 범위</th><th className="pr-2">오른 비율</th><th>평소(모든 날)</th></tr>
                  </thead>
                  <tbody>
                    {analog.horizons.map((h) => (
                      <tr key={h.months} className="border-t border-slate-100 dark:border-slate-800">
                        <td className="py-1 pr-2">{horizonLabel(h.months)}</td>
                        <td className="pr-2 font-semibold">{px(h.median)} <span className="font-normal text-slate-500">({pct(h.median)})</span></td>
                        <td className="pr-2">{px(h.p16)} ~ {px(h.p84)}</td>
                        <td className="pr-2">{(h.up * 100).toFixed(0)}%</td>
                        <td>{h.base ? `${pct(h.base.median)} · 오름 ${(h.base.up * 100).toFixed(0)}%` : "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-1 text-xs text-slate-400">
                같은 상태 = 3개월 수익률과 52주 고점 근접을 이 종목 자신의 과거에서 각각 셋으로 나눈 칸. 앞으로의 기간이 겹쳐 날들이 서로 독립이 아니므로 &quot;일수&quot; 보다 &quot;국면&quot; 수가 실제 표본에 가깝습니다.
              </p>
            </>
          )}
        </div>
      )}
      {scenario && (
        <div>
          <h2 className="mb-1 text-sm font-semibold">1년 시나리오 — 순자산 성장 × 자기 PBR 밴드</h2>
          <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
            {([["약세(밴드 20%)", scenario.bear], ["기본(밴드 중앙값)", scenario.base], ["강세(밴드 80%)", scenario.bull], ["PBR 그대로", scenario.hold]] as const).map(([이름, v]) => (
              <div key={이름} className="rounded-lg bg-slate-50 px-2 py-1.5 dark:bg-slate-900">
                <p className="text-slate-500">{이름}</p>
                <p className="font-semibold">{formatPrice(v, currency)}</p>
                <p className="text-slate-500">{close ? pct(v / close - 1) : ""}</p>
              </div>
            ))}
          </div>
          <p className="mt-1 text-xs text-slate-400">
            1년 뒤 주당순자산 = 지금 × (1 + ROE {pct(scenario.roe)} − 배당 몫 {pct(scenario.payout_part)}) = {pct(scenario.growth)} 성장. 그 순자산에 자기 3년 PBR 밴드의
            분위를 곱한 가격입니다 — 시장이 이 회사에 매겨 온 배수의 범위가 1년 뒤에도 같다는 가정입니다.
          </p>
        </div>
      )}
    </section>
  );
}
