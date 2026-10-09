"use client";

import { summaryCells, type Verdict } from "@/lib/analysis";

const TONE: Record<string, string> = {
  good: "border-red-200 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200",
  bad: "border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-200",
  neutral: "border-slate-200 bg-white text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200",
  none: "border-dashed border-slate-200 bg-slate-50 text-slate-400 dark:border-slate-800 dark:bg-slate-950",
};

/**
 * 한 줄 요약 카드 (docs/analysis.md 39장, 25.1062) — 가치·추세·수급·실적·재무 위험 다섯 칸.
 * 색은 각 장의 사실을 옮긴 것이다(빨강 = 좋은 쪽, 파랑 = 나쁜 쪽 — 국내 시세 색 관례). 판정·추천이 아니다.
 */
export default function SummaryStrip({ detail }: { detail: Verdict["detail"] | null | undefined }) {
  const cells = summaryCells(detail);
  if (cells.every((c) => c.tone === "none")) return null;
  return (
    <section aria-label="한 줄 요약" className="mb-3 grid grid-cols-2 gap-1.5 sm:grid-cols-5">
      {cells.map((c) => (
        <div key={c.key} className={`rounded-lg border px-2 py-1.5 text-xs ${TONE[c.tone]}`}>
          <div className="font-semibold">{c.label}</div>
          <div>{c.text}</div>
        </div>
      ))}
    </section>
  );
}
