"use client";

import type { AnalogData, ForecastData } from "@/lib/analysis";
import { formatPrice } from "@/lib/stockDetail";

/** 수평 자리(개월 → 0~1). 1·3·6·12개월이 한쪽에 몰리지 않게 제곱근 눈금 */
export function fanX(months: number): number {
  return Math.sqrt(months / 12);
}

/**
 * 예상 범위 부채꼴 (docs/analysis.md 10.4, 25.1044). 지금 종가에서 1·3·6·12개월로 퍼지는 50%·68%·90% 범위(CAPM)와
 * 비슷한 국면의 16~84% 범위(점선)를 한 그림에. 배치가 낸 값을 좌표로 옮길 뿐이다 — 새 계산이 없다.
 */
export default function ForecastFan({ f, analog, close, currency }: {
  f: ForecastData; analog?: AnalogData | null; close: number; currency: string;
}) {
  const hs = f.horizons.filter((h) => h.low90 !== undefined && h.high90 !== undefined && h.low68 !== undefined && h.high68 !== undefined);
  if (hs.length < 2 || close <= 0) return null;
  const an = (analog?.horizons ?? []).map((h) => ({ months: h.months, lo: close * (1 + h.p16), hi: close * (1 + h.p84), mid: close * (1 + h.median) }));
  const ys = [close, ...hs.flatMap((h) => [h.low90!, h.high90!]), ...an.flatMap((a) => [a.lo, a.hi])];
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  const W = 300;
  const H = 150;
  const PAD = 8;
  const x = (m: number) => PAD + fanX(m) * (W - 2 * PAD - 60);
  const y = (v: number) => PAD + (1 - (v - lo) / (hi - lo || 1)) * (H - 2 * PAD);
  const band = (key: "50" | "68" | "90") => {
    const top = [`${x(0)},${y(close)}`, ...hs.map((h) => `${x(h.months)},${y((key === "50" ? h.high50 : key === "68" ? h.high68 : h.high90)!)}`)];
    const bottom = hs.map((h) => `${x(h.months)},${y((key === "50" ? h.low50 : key === "68" ? h.low68 : h.low90)!)}`).reverse();
    return [...top, ...bottom].join(" ");
  };
  // 반반 범위(25.1055) — 그 뒤에 낸 의견에만 있다
  const 반반 = hs.every((h) => h.low50 !== undefined && h.high50 !== undefined);
  const 기대 = [`${x(0)},${y(close)}`, ...f.horizons.map((h) => `${x(h.months)},${y(h.expected)}`)].join(" ");
  const 끝 = hs[hs.length - 1];
  return (
    <figure className="mt-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-40 w-full" role="img" aria-label="예상 범위 부채꼴">
        <polygon points={band("90")} className="fill-violet-100 dark:fill-violet-950" />
        <polygon points={band("68")} className="fill-violet-200 dark:fill-violet-900" />
        {반반 && <polygon points={band("50")} className="fill-violet-300 dark:fill-violet-800" />}
        <line x1={x(0)} x2={W - 60} y1={y(close)} y2={y(close)} className="stroke-slate-400" strokeDasharray="2 3" strokeWidth={0.8} />
        <polyline points={기대} fill="none" className="stroke-violet-700 dark:stroke-violet-300" strokeWidth={1.5} />
        {an.length > 1 && (
          <>
            <polyline points={an.map((a) => `${x(a.months)},${y(a.hi)}`).join(" ")} fill="none" className="stroke-amber-600" strokeDasharray="4 2" strokeWidth={1} />
            <polyline points={an.map((a) => `${x(a.months)},${y(a.lo)}`).join(" ")} fill="none" className="stroke-amber-600" strokeDasharray="4 2" strokeWidth={1} />
          </>
        )}
        {[1, 3, 6, 12].map((m) => (
          <text key={m} x={x(m)} y={H - 1} textAnchor="middle" className="fill-slate-500 text-[8px]">{m === 12 ? "1년" : `${m}개월`}</text>
        ))}
        <text x={W - 56} y={y(끝.high90!) + 3} className="fill-slate-500 text-[8px]">{formatPrice(끝.high90!, currency)}</text>
        <text x={W - 56} y={y(끝.expected) + 3} className="fill-violet-700 text-[8px] dark:fill-violet-300">{formatPrice(끝.expected, currency)}</text>
        <text x={W - 56} y={y(끝.low90!) + 3} className="fill-slate-500 text-[8px]">{formatPrice(끝.low90!, currency)}</text>
      </svg>
      <figcaption className="text-xs text-slate-400">
        {반반 ? "가장 진한 띠 50% · 중간 띠 68% · 옅은 띠 90% 범위(CAPM)" : "진한 띠 68% · 옅은 띠 90% 범위(CAPM)"} · 선은 기대 경로 · 점선은 지금 종가{an.length > 1 ? " · 노란 점선은 비슷한 국면의 16~84% 범위" : ""}. 가로는 기간의 제곱근 눈금입니다.
      </figcaption>
    </figure>
  );
}
