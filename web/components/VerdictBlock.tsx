"use client";

import AnalysisExtras from "@/components/AnalysisExtras";
import PriceLadder from "@/components/PriceLadder";
import { VERDICT_LABEL, VERDICT_STYLE, outlookListLines, type Verdict } from "@/lib/analysis";

/** 종목 분석 의견 카드 본문 (docs/analysis.md). 결론·근거·반대 목소리·근거표 — 배치가 만든 값을 보이기만 한다 */
export default function VerdictBlock({ v, currency = "KRW" }: { v: Verdict; currency?: string }) {
  const reasons = v.detail?.reasons ?? [];
  const against = v.detail?.against ?? [];
  return (
    <div className="text-sm">
      <p className={`mb-2 rounded-lg border px-2.5 py-2 ${VERDICT_STYLE[v.verdict] ?? VERDICT_STYLE.waiting}`}>
        <span className="mr-1.5 font-semibold">{VERDICT_LABEL[v.verdict] ?? v.verdict}</span>
        {v.headline.replace(/^[^—]+—\s*/, "")}
      </p>
      {outlookListLines(v.detail?.outlook?.lines ?? []).length > 0 && (
        <div className="mb-2 rounded-lg bg-slate-50 px-2.5 py-2 dark:bg-slate-900">
          <p className="mb-0.5 text-xs font-semibold text-slate-600 dark:text-slate-300">가격·가치 진단</p>
          <ul className="space-y-0.5 text-slate-700 dark:text-slate-300">
            {outlookListLines(v.detail!.outlook!.lines).map((r) => <li key={r}>{r}</li>)}
          </ul>
          <p className="mt-1 text-xs text-slate-400">
            예상 주가는 화면 맨 위 표에 있습니다. 밴드 기준 가격은 &quot;PBR 이 그 분위면&quot; 의 가격이고, 증권사 목표가는 증권사의 예측입니다.
          </p>
        </div>
      )}
      {v.detail?.outlook?.ladder && <PriceLadder l={v.detail.outlook.ladder} currency={currency} />}
      <AnalysisExtras flows={v.detail?.outlook?.flows} twins={v.detail?.twins} />
      {reasons.length > 0 && (
        <ul className="mb-2 list-disc space-y-0.5 pl-5 text-slate-700 dark:text-slate-300">
          {reasons.map((r) => <li key={r}>{r}</li>)}
        </ul>
      )}
      {against.length > 0 && (
        <div className="mb-2">
          <p className="mb-0.5 text-xs font-semibold text-amber-700 dark:text-amber-300">반대 목소리 (참고 — 결론을 바꾸지 않습니다)</p>
          <ul className="list-disc space-y-0.5 pl-5 text-amber-800 dark:text-amber-200">
            {against.map((r) => <li key={r}>{r}</li>)}
          </ul>
        </div>
      )}
      {v.evidence.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-slate-500">근거표 {v.evidence.length}행 — 어느 표·언제·출처</summary>
          <div className="mt-1 overflow-x-auto">
            <table className="w-full min-w-[28rem] text-left">
              <thead className="text-slate-500">
                <tr><th className="py-1 pr-2">항목</th><th className="pr-2">값</th><th className="pr-2">기준</th><th className="pr-2">출처</th><th>기준일</th></tr>
              </thead>
              <tbody>
                {v.evidence.map((e, i) => (
                  <tr key={`${e.label}-${i}`} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="py-1 pr-2">{e.label}</td>
                    <td className="pr-2">{e.display ?? "-"}</td>
                    <td className="pr-2">{e.threshold ?? "-"}</td>
                    <td className="pr-2">{e.source ?? "-"}</td>
                    <td>{e.as_of ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      <p className="mt-2 text-xs text-slate-400">
        규칙의 결과를 모은 것입니다(점수 {v.score_as_of ?? "-"}{v.signal_as_of ? ` · 신호 ${v.signal_as_of}` : ""}). 자동 매매는 없습니다.
      </p>
    </div>
  );
}
