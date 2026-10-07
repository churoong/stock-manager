"use client";

import Link from "next/link";
import FlagCriteria from "@/components/FlagCriteria";
import CriteriaTable from "@/components/CriteriaTable";
import { STALE_AFTER_DAYS, horizonShort, parseCriteria } from "@/lib/recommend";
import { useEffect, useState } from "react";
import MarketTabs from "@/components/MarketTabs";
import { readJson } from "@/lib/http";
import { readSavedCountry, saveCountry, userTimeOf, type Country } from "@/lib/market";
import {
  SECTION_LABEL, SECTION_ORDER, deliveryLabel, flagAsOf, historySendLabel, pickCriteria, stabilityLabel, historyLabel,
  type HistoryRow, type ReportItem, type ReportRow,
} from "@/lib/reports";

/**
 * 일일 리포트 화면 (docs/reports.md).
 *
 * 본문은 텔레그램으로 보낸 글 **그대로** 보여 준다. 다시 그리지 않는다. 그 아래 재료를 표로
 * 곁들여 종목 상세로 이어 준다. 날짜를 고르면 지난 리포트를 본다.
 */

interface Payload {
  report: (ReportRow & { warnings: string[] }) | null;
  /** 가장 새 리포트 뒤로 빠진 리포트 수. 날짜를 골라 본 리포트이거나 달력을 모르면 null (docs/infra.md 25.235) */
  missed_reports?: number | null;
  items: ReportItem[];
  history: HistoryRow[];
  notes: string[];
}

function money(value: unknown, currency: unknown): string {
  if (typeof value !== "number") return "-";
  return currency === "USD"
    ? `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : `${Math.round(value).toLocaleString("ko-KR")}원`;
}

export default function ReportView() {
  const [country, setCountry] = useState<Country>("KR");
  const [ready, setReady] = useState(false);
  const [date, setDate] = useState<string | null>(null);
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setCountry(readSavedCountry());
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    let alive = true;
    setLoading(true);
    // 새로 읽는 동안 옛 본문·옛 오류를 두지 않는다 — 날짜만 바꿔도 응답이 올 때까지 옛 리포트가 남았다 (25.589, 교차검증). 날짜 목록은 둔다
    setError(null);
    setData((d) => (d ? { ...d, report: null, items: [], notes: [], missed_reports: null } : d));
    const query = new URLSearchParams({ market: country });
    if (date) query.set("date", date);
    void readJson<Payload>(`/api/reports?${query.toString()}`, undefined, { cache: true }).then((res) => {
      if (!alive) return;
      if (res.ok && res.data) {
        setData(res.data);
        setError(null);
      } else {
        setError(res.error ?? "리포트를 읽지 못했습니다");
        // **앞서 읽은 리포트를 남기지 않는다** (docs/infra.md 25.586, 감사). 실패하면 탭은 미국인데 국내 본문이, 드롭다운은 새 날짜인데
        // 옛 본문이 오류줄 아래에 그대로 보였다. 날짜 목록(history)은 다른 날로 옮겨 갈 수 있게 둔다
        setData((d) => (d ? { ...d, report: null, items: [], notes: [], missed_reports: null } : d));
      }
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [country, date, ready]);

  const report = data?.report ?? null;
  const missed = data?.missed_reports ?? null;
  const bySection = new Map<string, ReportItem[]>();
  for (const item of data?.items ?? []) {
    bySection.set(item.section, [...(bySection.get(item.section) ?? []), item]);
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <MarketTabs
          active={country}
          onChange={(next) => {
            setCountry(next);
            saveCountry(next);
            setDate(null);
            // 다른 시장의 리포트·날짜 목록을 새 탭에 남기지 않는다 (25.586)
            setData(null);
          }}
        />
        {data && data.history.length > 0 && (
          <select
            aria-label="리포트 날짜"
            value={date ?? report?.trade_date ?? ""}
            onChange={(e) => setDate(e.target.value || null)}
            className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-900"
          >
            {data.history.map((h) => (
              <option key={h.id} value={h.trade_date}>
                {h.trade_date} · {historySendLabel(h)}{h.status === "partial" ? " · 경고" : ""}
              </option>
            ))}
          </select>
        )}
      </div>

      {error && <p className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-950 dark:text-rose-300">{error}</p>}
      {loading && <p className="text-xs text-slate-500">읽는 중...</p>}
      {data?.notes.map((n) => (
        <p key={n} className="mb-3 text-xs text-slate-500 dark:text-slate-400">{n}</p>
      ))}

      {report && (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
            <span>거래일 {report.trade_date}</span>
            <span>{deliveryLabel(report)}</span>
            <span>만든 시각 {userTimeOf(report.generated_at)} KST</span>
            {missed !== null && missed >= 1 && (
              <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-800 dark:bg-amber-900 dark:text-amber-200">
                지난 리포트 — 그 뒤 거래일 {missed}번의 리포트가 없습니다
              </span>
            )}
          </div>
          {report.warnings.length > 0 && (
            <ul className="mb-3 list-disc pl-5 text-xs text-amber-700 dark:text-amber-300">
              {report.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          <pre className="mb-4 overflow-x-auto whitespace-pre-wrap rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs leading-relaxed dark:border-slate-800 dark:bg-slate-900">
            {report.summary_text}
          </pre>

          {SECTION_ORDER.filter((s) => bySection.has(s)).map((section) => (
            <section key={section} className="mb-4">
              <h2 className="mb-1 text-sm font-semibold">{SECTION_LABEL[section]}</h2>
              <ul className="divide-y divide-slate-100 text-xs dark:divide-slate-800">
                {(bySection.get(section) ?? []).map((item) => (
                  <li key={item.id} className="flex flex-wrap items-baseline gap-x-2 py-1.5">
                    <span className="w-5 text-slate-400">{item.rank}</span>
                    {item.stock_id ? (
                      <Link href={`/stocks/${item.stock_id}`} className="font-medium underline-offset-2 hover:underline">
                        {item.name ?? item.ticker ?? String(item.payload.ticker ?? "")}
                      </Link>
                    ) : (
                      <span className="font-medium">{String(item.payload.text ?? item.payload.ticker ?? "")}</span>
                    )}
                    {section === "recommend" && (
                      <span className="text-slate-500">
                        {horizonShort(item.payload.horizon)} · 종합 {typeof item.payload.total_score === "number" ? item.payload.total_score.toFixed(0) : "-"}
                        {/* 가중치 흔들기 — 이 점수가 설정값에 얼마나 기대는지 (docs/reports.md 3.2). 옛 리포트에는 없다 */}
                        {stabilityLabel(item.payload) && (
                          <span title={stabilityLabel(item.payload)?.title} className="cursor-help underline decoration-dotted underline-offset-2">
                            {" "}· {stabilityLabel(item.payload)?.text}
                          </span>
                        )}
                        {/* 추천 이력 — 몇 번째 추천이고 처음 본 날 종가 대비 얼마나 (docs/reports.md 3.3). 싣기 전 리포트에는 없다 */}
                        {historyLabel(item.payload) && <span>{" "}· {historyLabel(item.payload)}</span>}
                      </span>
                    )}
                    {section === "buy_signal" && (
                      <span className="text-slate-500">
                        {money(item.payload.amount, item.payload.currency)} · {typeof item.payload.weight_pct === "number" ? `${item.payload.weight_pct.toFixed(1)}%` : "-"}
                        {/* 상관으로 금액을 줄였으면 그 근거 (docs/infra.md 25.349) */}
                        {item.payload.note ? ` · ${String(item.payload.note)}` : ""}
                      </span>
                    )}
                    {section === "notice" && item.payload.kind === "excluded" && (
                      <span className="text-slate-500">2부 제외: {String(item.payload.reason ?? "")} {String(item.payload.detail ?? "")}</span>
                    )}
                    {section === "sell_flag" && (
                      <span className="text-slate-500">{String(item.payload.level ?? "")} · {String(item.payload.reason_code ?? item.payload.reason ?? "")}</span>
                    )}
                    {item.rationale_text && <span className="basis-full text-slate-500">{item.rationale_text}</span>}
                    {/*
                      1부 추천의 근거표 (docs/infra.md 25.349). **그날 신호가 쓴 근거**다 — 종목 링크는 오늘 데이터를 보여 주므로
                      지난 리포트의 추천을 확인하려면 여기에 있어야 한다. 0줄이면 CriteriaTable 이 빨간 경고를 그린다(25.174)
                    */}
                    {section === "recommend" && (
                      <span className="basis-full">
                        {pickCriteria(item.payload) === null ? (
                          <span className="text-[11px] text-slate-400">근거표를 싣기 전 리포트입니다 — 그날 근거는 남아 있지 않습니다</span>
                        ) : (
                          <details className="mt-1">
                            <summary className="cursor-pointer text-[11px] underline opacity-80">
                              근거 {parseCriteria(pickCriteria(item.payload)).length}개
                            </summary>
                            <CriteriaTable
                              rows={parseCriteria(pickCriteria(item.payload))}
                              caption={`신호 계산일 ${String(item.payload.as_of_date ?? "-")}`}
                              docPath="docs/signals.md"
                              staleAfterDays={STALE_AFTER_DAYS}
                            />
                          </details>
                        )}
                      </span>
                    )}
                    {/* 매도 플래그의 근거표 (docs/infra.md 25.262). 25.262 이전 리포트에는 근거가 실려 있지 않아 펼치지 않는다 */}
                    {section === "sell_flag" && Array.isArray(item.payload.criteria) && (
                      <span className="basis-full">
                        <FlagCriteria raw={JSON.stringify({ criteria: item.payload.criteria })} asOf={flagAsOf(item.payload)} />
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </>
      )}
    </div>
  );
}
