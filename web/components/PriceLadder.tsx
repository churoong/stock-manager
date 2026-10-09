"use client";

import { ladderWithNow, type LadderData, type LadderItem } from "@/lib/analysis";
import { formatPrice } from "@/lib/stockDetail";

/** 줄 색 — 색만으로 뜻을 전하지 않는다(이름을 늘 함께 쓴다) */
const KIND_STYLE: Record<LadderItem["kind"], string> = {
  high: "text-slate-600 dark:text-slate-300",
  band: "text-violet-700 dark:text-violet-300",
  consensus: "text-sky-700 dark:text-sky-300",
  range: "text-slate-500",
  signal: "text-emerald-700 dark:text-emerald-300",
  target: "text-emerald-700 dark:text-emerald-300",
  stop: "text-red-700 dark:text-red-300",
  criterion: "text-amber-700 dark:text-amber-300",
};

/**
 * 가격 사다리 (docs/analysis.md 12.2·12.3, 25.1038). 이 종목에 걸린 가격을 위에서 아래로 세우고 지금 종가를 그 사이에 끼운다.
 * 거리와 도달 확률은 배치가 낸 값이다 — 화면은 그리기만 한다.
 */
export default function PriceLadder({ l, currency }: { l: LadderData; currency: string }) {
  const pct = (x: number) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
  return (
    <div className="mb-2 rounded-lg border border-slate-200 px-2.5 py-2 dark:border-slate-800">
      <p className="mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">가격 사다리 — 이 종목에 걸린 가격들</p>
      {l.race && (
        <p className="mb-1.5 text-xs text-slate-700 dark:text-slate-300">
          목표가 {formatPrice(l.race.target, currency)}가 손절가 {formatPrice(l.race.stop, currency)}보다 <b>먼저</b> 닿을 확률{" "}
          <b>{(l.race.p * 100).toFixed(0)}%</b>
          {l.race.kelly !== undefined && l.race.kelly !== null && (
            <span className="block text-slate-500">
              켈리 참고 비중(이론상 최대) {(l.race.kelly * 100).toFixed(0)}% · 절반 켈리 {(l.race.kelly * 50).toFixed(0)}% — 비교용입니다. 실제 권장 비중은 상한·변동성 규칙을 따릅니다.
            </span>
          )}
        </p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[26rem] text-left text-xs">
          <thead className="text-slate-500">
            <tr>
              <th className="py-1 pr-2">가격</th><th className="pr-2">무엇</th><th className="pr-2">지금에서</th>
              {l.assumed && l.touch_months.map((m) => <th key={m} className="pr-2">{m === 12 ? "1년" : `${m}개월`} 안 닿을 확률</th>)}
            </tr>
          </thead>
          <tbody>
            {ladderWithNow(l).map((it, i) =>
              "now" in it ? (
                <tr key={`now-${i}`} className="border-y-2 border-slate-400 bg-slate-50 font-semibold dark:border-slate-500 dark:bg-slate-900">
                  <td className="py-1 pr-2">{formatPrice(it.price, currency)}</td>
                  <td className="pr-2" colSpan={2 + (l.assumed ? l.touch_months.length : 0)}>◀ 지금 종가</td>
                </tr>
              ) : (
                <tr key={`${it.label}-${i}`} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="py-1 pr-2">{formatPrice(it.price, currency)}</td>
                  <td className={`pr-2 ${KIND_STYLE[it.kind] ?? ""}`}>
                    {it.label}
                    {it.kind === "criterion" && (
                      <span className="ml-1 text-slate-500">({it.met ? "지금 충족" : it.need === "above" ? "넘으면 충족" : "밑돌면 충족"})</span>
                    )}
                  </td>
                  <td className="pr-2">{pct(it.dist)}</td>
                  {l.assumed && l.touch_months.map((m) => (
                    <td key={m} className="pr-2">{it.touch?.[String(m)] === undefined ? "-" : `${(it.touch[String(m)] * 100).toFixed(0)}%`}</td>
                  ))}
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        도달 확률은 예상 주가와 같은 가정(시장·베타로 낸 기대수익, 과거 변동성)으로 낸 통계적 값입니다. 가격 기준(노랑)은 신호 판정표의 문턱을 가격으로
        푼 것으로, 다른 조건이 그대로일 때의 값입니다.
      </p>
    </div>
  );
}
