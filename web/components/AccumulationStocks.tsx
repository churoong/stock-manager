"use client";

import Link from "next/link";
import { cachedFetch, peekCachedJson } from "@/lib/clientCache";
import QuickTrade from "@/components/QuickTrade";
import { money } from "@/lib/portfolio";
import { useEffect, useState } from "react";
import CriteriaTable from "@/components/CriteriaTable";
import {
  ACCUMULATION_CRITERIA_STALE_AFTER_DAYS,
  ACCUMULATION_STALE_AFTER_DAYS,
  ACCUMULATION_WARNINGS,
  ACCUMULATION_WARNINGS_US,
  dividendSummary,
  sectorCapWarning,
  displayName,
  type AccumulationRow,
  type FunnelStep,
} from "@/lib/accumulation";
import type { Country } from "@/lib/market";
import { DEFAULT_SETTINGS } from "@/lib/settings";
import { daysSince, parseCriteria } from "@/lib/recommend";

/**
 * 장기 적립 종목 (docs/accumulation.md 5장).
 *
 * 순위는 수익률이 아니라 안정성이다. 경고를 목록보다 먼저 보여 준다.
 * 장기 매수 신호와 다르다(1장). 같은 날 신호도 켜졌으면 참고 배지만 붙인다.
 */
export default function AccumulationStocks({ country }: { country: Country }) {
  // 나라를 바꾸면 상태를 새로 시작한다(key). 이전 나라 목록이 잠깐 섞여 보이지 않게
  return <AccumulationList key={country} country={country} />;
}

function stocksInit(country: Country): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ country }) };
}

interface StocksResponse {
  rows?: AccumulationRow[];
  funnel?: FunnelStep[];
  judged?: number;
  as_of?: string | null;
  notes?: string[];
  caps?: { stock: number; sector: number; warnings?: string[] } | null;
}

function AccumulationList({ country }: { country: Country }) {
  // 이미 읽은 응답이 있으면 그것으로 바로 그린다 (25.893)
  const [d0] = useState(() => peekCachedJson<StocksResponse>("/api/etf/stocks", stocksInit(country)));
  const [rows, setRows] = useState<AccumulationRow[]>(d0?.rows ?? []);
  const [funnel, setFunnel] = useState<FunnelStep[]>(d0?.funnel ?? []);
  const [judged, setJudged] = useState(d0?.judged ?? 0);
  const [asOf, setAsOf] = useState<string | null>(d0?.as_of ?? null);
  const [notes, setNotes] = useState<string[]>(d0?.notes ?? []);
  /** 설정의 비중 상한 (25.255). 못 받으면 기본값으로 그린다 */
  const [caps, setCaps] = useState<{ stock: number; sector: number; warnings?: string[] } | null>(d0?.caps ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(d0 === null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await cachedFetch("/api/etf/stocks", stocksInit(country));
        const data = await response.json();
        if (cancelled) return;
        if (!response.ok) {
          setError(data.errors?.join(", ") ?? "조회에 실패했습니다");
          return;
        }
        setRows(data.rows ?? []);
        setFunnel(data.funnel ?? []);
        setJudged(data.judged ?? 0);
        setAsOf(data.as_of ?? null);
        setNotes(data.notes ?? []);
        setCaps(data.caps ?? null);
      } catch {
        if (!cancelled) setError("조회에 실패했습니다");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [country]);

  if (loading) return <p className="py-8 text-sm text-slate-500">불러오는 중…</p>;
  if (error) {
    return (
      <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-950 dark:text-rose-300">
        {error}
      </p>
    );
  }

  const age = daysSince(asOf);
  const stale = age !== null && age > ACCUMULATION_STALE_AFTER_DAYS;

  return (
    <div>
      {notes.map((note) => (
        <p key={note} className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          {note}
        </p>
      ))}

      <ul className="mb-3 space-y-1 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-900 dark:bg-amber-950 dark:text-amber-200">
        {[
          ...ACCUMULATION_WARNINGS,
          sectorCapWarning(caps?.stock ?? DEFAULT_SETTINGS.max_weight_per_stock, caps?.sector ?? DEFAULT_SETTINGS.max_weight_per_sector),
          ...(country === "US" ? ACCUMULATION_WARNINGS_US : []),
          // 설정이 범위를 벗어났거나 종목 상한을 섹터 상한으로 줄였으면 그 사실 (docs/infra.md 25.357)
          ...(caps?.warnings ?? []),
        ].map((w) => (
          <li key={w}>· {w}</li>
        ))}
      </ul>

      <div className="mb-2 flex flex-wrap items-baseline gap-x-2 text-xs text-slate-500">
        <span>
          {judged > 0 ? `유니버스 ${judged}종목 중 ${rows.length}개 통과 · 안정성 순` : null}
        </span>
        <span className={`ml-auto ${stale ? "text-amber-700 dark:text-amber-300" : "text-slate-400"}`}>
          판정일 {asOf ?? "-"}
          {age !== null && age > 0 ? ` (${age}일 전)` : ""}
        </span>
      </div>

      <div className="flex flex-col gap-2">
        {rows.map((row) => (
          <StockCard key={row.stock_id} row={row} country={country} />
        ))}
      </div>

      {funnel.length > 0 ? (
        <details className="my-4 rounded-lg border border-slate-200 text-xs dark:border-slate-800">
          <summary className="cursor-pointer select-none px-3 py-2 text-slate-600 dark:text-slate-300">
            왜 이것뿐인가 <span className="ml-1 text-slate-400">조건마다 처음 걸려 빠진 수</span>
          </summary>
          <table className="w-full border-t border-slate-200 dark:border-slate-800">
            <tbody>
              {funnel.map((step) => (
                <tr key={step.gate} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="px-3 py-1.5 text-slate-400">{step.gate}</td>
                  <td className="px-3 py-1.5">{step.label}</td>
                  <td className="px-3 py-1.5 text-right">−{step.failed}</td>
                </tr>
              ))}
              <tr className="border-t border-slate-200 font-medium dark:border-slate-800">
                <td className="px-3 py-1.5" />
                <td className="px-3 py-1.5">통과</td>
                <td className="px-3 py-1.5 text-right">{rows.length}</td>
              </tr>
            </tbody>
          </table>
          <p className="border-t border-slate-100 px-3 py-2 text-slate-500 dark:border-slate-800">
            {country === "US"
              ? "은행·보험은 부채비율 조건에서 빠집니다."
              : "은행·금융지주는 업종 자료가 없어 매출 유무로 대신 걸러 통째로 빠집니다."}
          </p>
        </details>
      ) : null}
    </div>
  );
}

function StockCard({ row, country }: { row: AccumulationRow; country: Country }) {
  const [open, setOpen] = useState(false);
  const criteria = parseCriteria(row.rationale_data);
  const dividend = dividendSummary(row.rationale_data);
  return (
    <article className="rounded-xl border border-slate-200 dark:border-slate-800">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="w-full px-3 py-2.5 text-left">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-xs text-slate-400">{row.rank_in_group}</span>
          <span className="font-semibold">{displayName(row.name_ko) ?? row.ticker}</span>
          <span className="text-xs text-slate-500">
            {row.ticker} · {row.market}
          </span>
          {row.long_signal_on ? (
            <span
              className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
              title="이 판정을 낼 때 마지막으로 계산한 신호에서 장기 매수 신호도 켜져 있었습니다(그 뒤 바뀌었을 수 있음). 참고용이며 순위에 넣지 않았습니다"
            >
              장기 신호
            </span>
          ) : null}
          <span className="ml-auto text-sm">
            안정성 <b>{row.score !== null ? Math.round(row.score) : "-"}</b>
            <span className="ml-2 text-xs text-slate-400">{open ? "▲" : "▼"}</span>
          </span>
        </div>
        {/* 가장 새 종가와 그 날짜 (25.894). 판정은 월 1회라 판정일과 다를 수 있다 */}
        {row.close !== null && row.close !== undefined ? (
          <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-300">
            현재가 {money(row.close, row.currency ?? (country === "US" ? "USD" : "KRW"))}
            {row.price_date ? <span className="ml-1 text-slate-400">({row.price_date} 종가)</span> : null}
          </p>
        ) : null}
        {dividend ? <p className="mt-0.5 text-xs text-slate-500">배당 {dividend}</p> : null}
        <p className={`mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-300 ${open ? "" : "line-clamp-2"}`}>
          {row.rationale_text}
        </p>
      </button>
      {/* 펼쳤으면 0줄이어도 그린다 — 그때는 빨간 경고가 뜬다 (docs/infra.md 25.71·25.174) */}
      {open ? (
        <div className="border-t border-slate-100 px-3 pb-3 pt-1 dark:border-slate-800">
          <div className="mt-1 mb-1 flex flex-wrap items-center justify-end gap-3">
            <Link href={`/stocks/${row.stock_id}`} className="text-xs text-blue-700 underline dark:text-blue-300">종목 상세</Link>
            {/* 그 자리에서 매매 입력 (25.894). 종목 상세와 같은 줄, 폼은 아랫줄 전체 (25.895) */}
            <QuickTrade
              stock={{
                id: row.stock_id,
                ticker: row.ticker,
                name: displayName(row.name_ko) ?? row.ticker,
                country,
                currency: row.currency ?? (country === "US" ? "USD" : "KRW"),
                market: row.market,
              }}
              horizon="long"
            />
          </div>
          <CriteriaTable
            rows={criteria}
            caption={`판정일 ${row.as_of_date} · FY${row.fiscal_year_to - 4}~${row.fiscal_year_to}`}
            docPath={row.market === "KOSPI" || row.market === "KOSDAQ" ? "docs/accumulation.md 2장" : "docs/accumulation.md 7장"}
            staleAfterDays={ACCUMULATION_CRITERIA_STALE_AFTER_DAYS}
          />
        </div>
      ) : null}
    </article>
  );
}
