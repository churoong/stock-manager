"use client";

import { MODEL_LABEL, TRACK_MIN_SAMPLE, horizonLabel, trackRows, type TrackData, type TrackRow } from "@/lib/analysis";

/**
 * 예측 성적표 (docs/analysis.md 11장, 25.1037). 날마다 낸 예측을 기간이 지나 실제 가격과 견준 누계 — 배치가 센 수를 그린다.
 * 표본이 TRACK_MIN_SAMPLE 건 미만이면 비율 대신 "표본 n건" 만 보인다(우연이 크다).
 */
export default function ForecastTrack({ t }: { t: TrackData }) {
  const 종목 = trackRows(t.stock);
  const 시장 = trackRows(t.market);
  const pct = (x: number | null, ok: boolean) => (x === null ? "-" : ok ? `${(x * 100).toFixed(0)}%` : "표본 적음");
  const 줄 = (r: TrackRow) => (
    <tr key={`${r.model}-${r.months}`} className="border-t border-slate-100 dark:border-slate-800">
      <td className="py-1 pr-2">{MODEL_LABEL[r.model] ?? r.model}</td>
      <td className="pr-2">{horizonLabel(r.months)}</td>
      <td className="pr-2">{r.n.toLocaleString()}건</td>
      <td className="pr-2">{pct(r.in68, r.enough)}</td>
      <td className="pr-2">{pct(r.dir, r.enough)}</td>
      <td>{r.err === null || !r.enough ? "-" : `${(r.err * 100).toFixed(1)}%`}</td>
    </tr>
  );
  const 표 = (rows: TrackRow[], 제목: string) => (
    <div className="mb-2 overflow-x-auto">
      <p className="mb-0.5 text-xs font-semibold text-slate-600 dark:text-slate-300">{제목}</p>
      <table className="w-full min-w-[24rem] text-left text-xs">
        <thead className="text-slate-500">
          <tr><th className="py-1 pr-2">모델</th><th className="pr-2">기간</th><th className="pr-2">평가</th><th className="pr-2">68% 범위 안</th><th className="pr-2">방향 적중</th><th>평균 오차</th></tr>
        </thead>
        <tbody>{rows.map(줄)}</tbody>
      </table>
    </div>
  );
  return (
    <section id="forecast-track" className="mb-4 rounded-xl border border-slate-200 p-3 dark:border-slate-800">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">예측 성적표</h2>
        <span className="text-xs text-slate-500">{t.since ? `${t.since}부터 쌓는 중` : ""}</span>
      </div>
      {!종목.length && !시장.length ? (
        <p className="text-xs text-slate-500">
          날마다 낸 예측을 쌓고 있습니다. 첫 성적은 {t.first_due ?? "한 달 뒤"}부터 나옵니다(가장 짧은 1개월 예측의 기간이 찬 날).
        </p>
      ) : (
        <>
          {종목.length > 0 && 표(종목, "이 종목")}
          {시장.length > 0 && 표(시장, "시장 전체(같은 시장 모든 종목)")}
        </>
      )}
      <p className="mt-1 text-xs text-slate-400">
        68% 범위 안 = 실제 가격이 그때 낸 68% 범위에 든 비율(잘 맞춘 범위면 68% 근처). 방향 적중 = 오름·내림을 맞힌 비율. 평균 오차 = 로그 수익률 차이의
        평균. 표본 {TRACK_MIN_SAMPLE}건 미만이면 비율을 말하지 않습니다.
      </p>
    </section>
  );
}
