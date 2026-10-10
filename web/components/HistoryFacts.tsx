"use client";

import { REPORT_NAME, seasonTone, type DistLite, type HistoryData } from "@/lib/analysis";

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
  const ea = h.earnings;
  const so = h.sources;
  const sh = h.short;
  const mv = h.moves;
  const sc = h.scenarios;
  const vr = h.vol_regime;
  if (!dd && !t && !Object.keys(se).length && !bo && !ea && !so && !sh && !mv && !sc && !vr) return null;
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
            {dd.worst?.length > 0 && (
              <ul className="mt-1 space-y-0.5 text-xs" aria-label="가장 깊었던 낙폭">
                <li className="text-slate-500">가장 깊었던 {dd.worst.length}번</li>
                {dd.worst.map((w) => (
                  <li key={w.peak}>
                    {w.peak}→{w.trough} <b>{pct(w.depth)}</b> ·{" "}
                    {w.recovered ? `${w.recovered} 회복 (고점부터 ${w.days}거래일)` : "아직 회복 못 함"}
                  </li>
                ))}
              </ul>
            )}
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
        {vr && (
          <div>
            <h3 className="mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">변동성 국면 — 지금 {(vr.now * 100).toFixed(0)}% (자기 이력 {(vr.pct * 100).toFixed(0)}% 분위)</h3>
            <table className="w-full text-left text-xs">
              <thead className="text-slate-500"><tr><th className="py-0.5 pr-2">한 달 변동성</th><th className="pr-2">1개월 뒤</th><th>3개월 뒤</th></tr></thead>
              <tbody>
                {["0", "1", "2"].map((k) => {
                  const b = vr.buckets[k] ?? {};
                  const 칸 = (d: DistLite | null | undefined) => (d ? `${pct(d.median)} · 오름 ${(d.up * 100).toFixed(0)}%` : "-");
                  return (
                    <tr key={k} className={`border-t border-slate-100 dark:border-slate-800 ${Number(k) === vr.bucket ? "font-semibold" : ""}`}>
                      <td className="py-0.5 pr-2">{["조용한 편", "보통", "시끄러운 편"][Number(k)]}{Number(k) === vr.bucket ? " ◀ 지금" : ""}</td>
                      <td className="pr-2">{칸(b["1"])}</td>
                      <td>{칸(b["3"])}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="mt-1 text-[11px] text-slate-400">칸 = 이 종목 자기 이력의 한 달 변동성 삼분위(경계 {(vr.edges[0] * 100).toFixed(0)}% · {(vr.edges[1] * 100).toFixed(0)}%).</p>
          </div>
        )}
        {sc && (sc.down.stock || sc.up.stock) && (
          <div>
            <h3 className="mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">시장 시나리오 — 시장이 1년에 {(sc.move * 100).toFixed(0)}% 넘게 움직이면</h3>
            <ul className="space-y-0.5 text-xs">
              {([["down", "빠진"], ["up", "오른"]] as const).map(([k, 말]) => {
                const x = sc[k];
                return x.stock ? (
                  <li key={k}>
                    {말} 경로 {x.n}개(지수 중앙값 {pct(x.index!)}): 이 종목 중앙값 <b>{pct(x.stock[1])}</b> · 68% {pct(x.stock[0])}~{pct(x.stock[2])}
                    {sc.beta !== null ? <span className="text-slate-500"> (베타 {sc.beta.toFixed(2)}로 단순 환산 {pct(x.index! * sc.beta)})</span> : null}
                  </li>
                ) : (
                  <li key={k} className="text-slate-400">{말} 경로가 {x.n}개뿐이라 분포를 내지 않습니다</li>
                );
              })}
            </ul>
            <p className="mt-1 text-[11px] text-slate-400">종목과 지수의 같은 날 수익을 한 쌍으로 다시 뽑은 1년 경로 {sc.paths.toLocaleString()}개에서 셉니다(함께 무너지는 꼬리가 남습니다). 평균은 뺐습니다.</p>
          </div>
        )}
        {mv && Object.keys(mv.w).length > 0 && (
          <div>
            <h3 className="mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">움직임 분해 — 시장·업종·이 종목만 (베타 {mv.beta.toFixed(2)})</h3>
            <table className="w-full text-left text-xs">
              <thead className="text-slate-500"><tr><th className="py-0.5 pr-2">창</th><th className="pr-2">종목</th><th className="pr-2">시장 몫</th><th className="pr-2">업종 몫</th><th>이 종목만</th></tr></thead>
              <tbody>
                {Object.entries(mv.w).map(([n, x]) => (
                  <tr key={n} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="py-0.5 pr-2">{n}거래일</td>
                    <td className="pr-2 font-medium">{pct(x.stock)}</td>
                    <td className="pr-2">{pct(x.market)}</td>
                    <td className="pr-2">{x.sector === null ? "-" : pct(x.sector)}</td>
                    <td>{pct(x.own)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {mv.leaders && (
              <p className="mt-1 text-xs">
                업종 대형주(시총 상위 {mv.leaders.n}/{mv.leaders.of}종목{mv.leaders.self_leader ? ", 이 종목 포함" : ""}):{" "}
                {Object.entries(mv.leaders.w).map(([n, x]) => `${n}거래일 평균 ${pct(x.leaders)}`).join(" · ")}
              </p>
            )}
            <p className="mt-1 text-[11px] text-slate-400">시장 몫 = 베타 × 지수, 업종 몫 = 같은 업종 평균 − 지수, 나머지가 이 종목만의 몫. ~{mv.until}.</p>
          </div>
        )}
        {so && (
          <div>
            <h3 className="mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">상승의 출처 ({so.from_year}→{so.to_year} 사업보고서)</h3>
            <p className="text-xs">
              주가 <b>{pct(so.price)}</b> = 순이익 {pct(so.earnings)} × 배수(PER) {pct(so.multiple)}
              {so.dividends !== null ? ` · 그 사이 배당 ${pct(so.dividends)}` : ""}
            </p>
            <p className="mt-1 text-[11px] text-slate-400">{so.since}~{so.until}. 주식 수가 그대로라고 본 근사입니다(증자·소각이 있으면 어긋남).</p>
          </div>
        )}
        {ea && (ea.recent.length > 0 || ea.grew || ea.shrank) && (
          <div>
            <h3 className="mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">실적 발표 반응 ({ea.basis}, {ea.events}번)</h3>
            {(ea.grew || ea.shrank) && (
              // 요약 — 진단 줄 "실적 발표 반응(" 은 목록에서 빼고 여기서 그린다 (HISTORY_LINE_PREFIXES, 25.1084)
              <ul className="mb-1 space-y-0.5 text-xs">
                {([["영업이익이 늘어난 발표", ea.grew], ["줄어든 발표", ea.shrank]] as const).map(([이름, x]) =>
                  x ? (
                    <li key={이름}>
                      {이름} {x.n}번: 5거래일 시장 대비 평균 <b>{pct(x.avg)}</b> · 오른 것 {x.up}번
                    </li>
                  ) : null,
                )}
              </ul>
            )}
            {ea.recent.length > 0 && <table className="w-full text-left text-xs">
              <thead className="text-slate-500"><tr><th className="py-0.5 pr-2">접수일</th><th className="pr-2">보고서</th><th className="pr-2">영업이익 전년비</th><th className="pr-2">1일</th><th>5일(시장 대비)</th></tr></thead>
              <tbody>
                {ea.recent.map((x) => (
                  <tr key={x.date + x.report_code} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="py-0.5 pr-2">{x.date}</td>
                    <td className="pr-2">{x.fiscal_year} {REPORT_NAME[x.report_code] ?? x.report_code}</td>
                    <td className="pr-2">{x.yoy === null ? "-" : pct(x.yoy)}</td>
                    <td className="pr-2">{pct(x.r1)}</td>
                    <td>{x.x5 === null ? pct(x.r5) : pct(x.x5)}</td>
                  </tr>
                ))}
              </tbody>
            </table>}
            <p className="mt-1 text-[11px] text-slate-400">접수일 전날 종가 기준. 컨센서스 대비 서프라이즈가 아니라 전년 같은 보고서 대비입니다.</p>
          </div>
        )}
        {sh && sh.events > 0 && sh.h["5"] && (
          <div>
            <h3 className="mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">공매도 급증 뒤 ({sh.since}~, {sh.events}번)</h3>
            <ul className="space-y-0.5 text-xs">
              {["5", "20"].map((m) => {
                const a = sh.h[m];
                const b = sh.base[m];
                return a ? <li key={m}>{m}거래일 뒤 중앙값 <b>{pct(a.median)}</b> · 오름 {(a.up * 100).toFixed(0)}% {b ? `(평소 ${pct(b.median)} · ${(b.up * 100).toFixed(0)}%)` : ""}</li> : null;
              })}
            </ul>
            <p className="mt-1 text-[11px] text-slate-400">급증 = 공매도 비중이 앞 20거래일 중앙값의 2배 이상. 수집이 2026-10 에 시작해 표본이 적습니다.</p>
          </div>
        )}
      </div>
    </section>
  );
}
