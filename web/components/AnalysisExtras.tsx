"use client";

import Link from "next/link";
import type { FlowCard, Twin } from "@/lib/analysis";

/**
 * 종목 분석 의견 카드의 해석 묶음 (docs/analysis.md 16·18장, 25.1041) — 수급 흐름(국내)과 팩터 모양이 닮은 종목.
 * 배치가 낸 값을 그리기만 한다.
 */
export default function AnalysisExtras({ flows, twins }: { flows?: FlowCard | null; twins?: Twin[] }) {
  if (!flows && !twins?.length) return null;
  const 억 = (x: number) => `${x >= 0 ? "+" : ""}${x.toLocaleString(undefined, { maximumFractionDigits: 1 })}억`;
  return (
    <div className="mb-2 grid gap-2 sm:grid-cols-2">
      {flows && Object.keys(flows.windows).length > 0 && (
        <div className="rounded-lg border border-slate-200 px-2.5 py-2 text-xs dark:border-slate-800">
          <p className="mb-1 font-semibold text-slate-600 dark:text-slate-300">수급 흐름 (기준 {flows.latest})</p>
          <table className="mb-1 w-full text-left">
            <thead className="text-slate-500">
              <tr><th className="pr-2">창</th><th className="pr-2">외국인</th><th className="pr-2">기관</th><th>개인</th></tr>
            </thead>
            <tbody>
              {Object.entries(flows.windows).map(([w, x]) => (
                <tr key={w} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="py-0.5 pr-2">{w}거래일</td>
                  <td className={`pr-2 ${x.frgn < 0 ? "text-blue-700 dark:text-blue-300" : "text-red-700 dark:text-red-300"}`}>{억(x.frgn)}</td>
                  <td className={`pr-2 ${x.orgn < 0 ? "text-blue-700 dark:text-blue-300" : "text-red-700 dark:text-red-300"}`}>{억(x.orgn)}</td>
                  <td>{억(x.prsn)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="space-y-0.5 text-slate-600 dark:text-slate-300">
            {flows.lines.filter((l) => !l.includes("거래일 순매수:")).map((l) => <li key={l}>· {l}</li>)}
          </ul>
        </div>
      )}
      {twins && twins.length > 0 && (
        <div className="rounded-lg border border-slate-200 px-2.5 py-2 text-xs dark:border-slate-800">
          <p className="mb-1 font-semibold text-slate-600 dark:text-slate-300">점수 모양이 닮은 종목 (같은 시장)</p>
          <ul className="space-y-1">
            {twins.map((t) => (
              <li key={t.stock_id} className="flex items-baseline justify-between gap-2">
                <Link href={`/stocks/${t.stock_id}`} className="truncate text-sky-700 underline-offset-2 hover:underline dark:text-sky-300">
                  {t.name} <span className="text-slate-400">{t.ticker}</span>
                </Link>
                <span className="shrink-0 text-slate-500">
                  종합 {t.total === null ? "-" : t.total.toFixed(1)} · 거리 {t.dist}
                  {t.signal && <span className="ml-1 rounded bg-emerald-100 px-1 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200">신호</span>}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-1 text-slate-400">밸류·퀄리티·성장·모멘텀·리스크 다섯 점수의 거리가 가까운 순. 같은 성격의 대안을 찾는 데 씁니다.</p>
        </div>
      )}
    </div>
  );
}
