"use client";

import { FACTOR_NAME, type Decomposition } from "@/lib/analysis";

/** 막대 너비(%) — 가장 큰 몫을 100 으로. 표시용 비례다 */
export function barWidth(v: number, max: number): number {
  return max > 0 ? Math.min(100, (Math.abs(v) / max) * 100) : 0;
}

/**
 * 종합 점수 분해 (docs/analysis.md 26장, 25.1051). 팩터 몫(점수 × 가중치)과 4주 동안 몫의 변화 — 배치가 낸 값을 막대로.
 */
export default function ScoreWaterfall({ d }: { d: Decomposition }) {
  const 몫 = [...d.parts.map((p) => ({ k: p.factor, v: p.contrib, note: `${p.score.toFixed(0)}점 × ${p.weight.toFixed(0)}%` })),
    ...(d.sentiment ? [{ k: "sentiment", v: d.sentiment, note: "감성" }] : [])];
  const 최대 = Math.max(...몫.map((x) => Math.abs(x.v)), 1);
  const 변화 = d.change ? Object.entries(d.change).filter(([, v]) => v !== 0).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])) : [];
  const 변화최대 = Math.max(...변화.map(([, v]) => Math.abs(v)), 0.1);
  const 이름 = (k: string) => (k === "sentiment" ? "감성" : FACTOR_NAME[k] ?? k);
  return (
    <div className="mb-2 rounded-lg border border-slate-200 px-2.5 py-2 text-xs dark:border-slate-800">
      <p className="mb-1 font-semibold text-slate-600 dark:text-slate-300">종합 점수 {d.total.toFixed(1)} — 어디서 왔나</p>
      <ul className="space-y-0.5">
        {몫.map((x) => (
          <li key={x.k} className="flex items-center gap-2">
            <span className="w-14 shrink-0 text-slate-500">{이름(x.k)}</span>
            <span className="relative h-3 flex-1 rounded bg-slate-100 dark:bg-slate-800">
              <span className={`absolute inset-y-0 left-0 rounded ${x.v >= 0 ? "bg-violet-400" : "bg-rose-400"}`} style={{ width: `${barWidth(x.v, 최대)}%` }} />
            </span>
            <span className="w-28 shrink-0 text-right text-slate-600 dark:text-slate-300">{x.v >= 0 ? "+" : ""}{x.v.toFixed(1)} <span className="text-slate-400">({x.note})</span></span>
          </li>
        ))}
      </ul>
      {변화.length > 0 && (
        <>
          <p className="mb-0.5 mt-2 font-semibold text-slate-600 dark:text-slate-300">{d.since} 뒤 몫의 변화</p>
          <ul className="space-y-0.5">
            {변화.map(([k, v]) => (
              <li key={k} className="flex items-center gap-2">
                <span className="w-14 shrink-0 text-slate-500">{이름(k)}</span>
                <span className="relative h-3 flex-1">
                  <span className="absolute inset-y-0 left-1/2 w-px bg-slate-300 dark:bg-slate-600" />
                  <span
                    className={`absolute inset-y-0 rounded ${v >= 0 ? "left-1/2 bg-red-400" : "right-1/2 bg-blue-400"}`}
                    style={{ width: `${barWidth(v, 변화최대) / 2}%` }}
                  />
                </span>
                <span className="w-12 shrink-0 text-right">{v >= 0 ? "+" : ""}{v.toFixed(1)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
