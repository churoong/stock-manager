"use client";

import { seasonTone, type HistoryData } from "@/lib/analysis";

/**
 * 자기 시세 이력의 사실들 (docs/analysis.md 31~34장, 25.1057) — 낙폭 회복 시간표·최악의 한 달·계절성 달력·52주 신고가 뒤.
 * 배치(`services/history`)가 낸 횟수·분포를 그리기만 한다. 색은 새 문턱이 아니라 사실(오른 비율이 절반 위인가)을 옮긴다.
 */
export default function HistoryFacts({ h }: { h: HistoryData }) {
  const pct = (x: number) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
  const dd = h.drawdown;
  const t = h.tail;
  const se = h.season?.months ?? {};
  const bo = h.breakout;
  if (!dd && !t && !Object.keys(se).length && !bo) return null;
  return (
    <section id="history-facts" className="mb-4 rounded-xl border border-slate-200 p-3 dark:border-slate-800">
      <h2 className="mb-2 text-sm font-semibold">이 종목의 지난 시세에서</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {dd && (
          <div>
            <h3 className="mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">낙폭 회복 시간표</h3>
            <p className="mb-1 text-xs">지금 고점({dd.peak_date}) 대비 <b>{pct(dd.now)}</b> · 고점 뒤 {dd.days_since_peak}거래일</p>
            <table className="w-full text-left text-xs">
              <thead className="text-slate-500"><tr><th className="py-0.5 pr-2">깊이</th><th className="pr-2">횟수</th><th className="pr-2">회복 중앙값</th><th>미회복</th></tr></thead>
              <tbody>
                {Object.entries(dd.levels).map(([lv, s]) => (
                  <tr key={lv} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="py-0.5 pr-2">−{(Number(lv) * 100).toFixed(0)}% 이상</td>
                    <td className="pr-2">{s.n}번</td>
                    <td className="pr-2">{s.median_days === null ? "-" : `${Math.round(s.median_days)}거래일`}</td>
                    <td>{s.open}번</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-1 text-[11px] text-slate-400">회복 = 그 깊이를 처음 지난 날부터 고점을 되찾기까지. {dd.since}~{dd.until} 수정주가.</p>
          </div>
        )}
        {t && (
          <div>
            <h3 className="mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">최악의 경우 (실제 분포의 꼬리)</h3>
            <ul className="space-y-0.5 text-xs">
              <li>한 달: 가장 나쁜 5% 평균 <b>{pct(t.m1.cvar)}</b> · 문턱 {pct(t.m1.var)} · 최악 {pct(t.m1.worst)}</li>
              <li>하루: 가장 나쁜 5% 평균 <b>{pct(t.d1.cvar)}</b> · 문턱 {pct(t.d1.var)} · 최악 {pct(t.d1.worst)}</li>
              <li className="text-slate-500">예: 100만 원이면 나쁜 한 달 평균 {Math.round(t.m1.cvar * 1_000_000).toLocaleString()}원</li>
            </ul>
          </div>
        )}
        {Object.keys(se).length > 0 && (
          <div>
            <h3 className="mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">계절성 달력 — 우연이 큽니다(판정에 쓰지 않음)</h3>
            <div className="grid grid-cols-6 gap-1 text-center text-[11px]">
              {Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, "0")).map((m) => {
                const x = se[m];
                const tone = x ? seasonTone(x.up, x.n) : null;
                const cls = tone === "up" ? "bg-red-50 dark:bg-red-950" : tone === "down" ? "bg-blue-50 dark:bg-blue-950" : "bg-slate-50 dark:bg-slate-900";
                return (
                  <div key={m} className={`rounded px-1 py-0.5 ${cls}`}>
                    <div className="font-semibold">{Number(m)}월</div>
                    <div>{x ? `${x.up}/${x.n}` : "-"}</div>
                    <div className="text-slate-500">{x ? pct(x.avg) : ""}</div>
                  </div>
                );
              })}
            </div>
            <p className="mt-1 text-[11px] text-slate-400">칸 = 오른 해 수/전체 해 수 · 평균 월수익.</p>
          </div>
        )}
        {bo && bo.h?.["1"] && (
          <div>
            <h3 className="mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">52주 신고가 뒤</h3>
            <p className="mb-1 text-xs">지난 {bo.events}번{bo.last ? ` (마지막 ${bo.last})` : ""}</p>
            <table className="w-full text-left text-xs">
              <thead className="text-slate-500"><tr><th className="py-0.5 pr-2">기간</th><th className="pr-2">신고가 뒤</th><th className="pr-2">이 종목 평소</th><th>시장 전체 신고가 뒤</th></tr></thead>
              <tbody>
                {["1", "3"].map((m) => {
                  const a = bo.h[m];
                  const b = bo.base[m];
                  const c = h.market_breakout?.[m];
                  return a ? (
                    <tr key={m} className="border-t border-slate-100 dark:border-slate-800">
                      <td className="py-0.5 pr-2">{m}개월</td>
                      <td className="pr-2">{pct(a.median)} · 오름 {(a.up * 100).toFixed(0)}%</td>
                      <td className="pr-2">{b ? `${pct(b.median)} · ${(b.up * 100).toFixed(0)}%` : "-"}</td>
                      <td>{c ? `${pct(c.median)} · ${(c.up * 100).toFixed(0)}%` : "-"}</td>
                    </tr>
                  ) : null;
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
