"use client";

import { useCallback, useEffect, useState } from "react";
import { cachedFetch, peekCachedJson } from "@/lib/clientCache";
import { money } from "@/lib/portfolio";
import QuickTrade from "@/components/QuickTrade";
import CriteriaTable from "@/components/CriteriaTable";
import { ETF_STALE_AFTER_DAYS, formatAssets, formatPct } from "@/lib/etf";
import { parseWarnings, type SatelliteGroup, type SatelliteRow } from "@/lib/etfSatellite";
import type { Country } from "@/lib/market";
import { daysSince, parseCriteria } from "@/lib/recommend";

/**
 * 위성 ETF — 배당 · 업종 · 테마 (docs/etf.md 10장).
 *
 * 위성은 핵심을 대신하지 않는다. 그 말을 목록보다 먼저 보여 준다. 테마에는 생존율 경고를 붙인다.
 * 비중 수치(예: 20%)는 적지 않는다. 외부 표준이 없다(10.0).
 */
function satelliteInit(country: Country): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ country }) };
}

interface SatelliteResponse {
  groups?: SatelliteGroup[];
  as_of?: string | null;
  notes?: string[];
}

export default function SatelliteList({ country }: { country: Country }) {
  // 이미 읽은 응답이 있으면 그것으로 바로 그린다 (25.893)
  const [d0] = useState(() => peekCachedJson<SatelliteResponse>("/api/etf/satellite", satelliteInit(country)));
  const [groups, setGroups] = useState<SatelliteGroup[]>(d0?.groups ?? []);
  const [asOf, setAsOf] = useState<string | null>(d0?.as_of ?? null);
  const [notes, setNotes] = useState<string[]>(d0?.notes ?? []);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(d0 === null);
  const [active, setActive] = useState<string>("배당");

  const load = useCallback(async (target: Country) => {
    if (peekCachedJson("/api/etf/satellite", satelliteInit(target)) === null) setLoading(true);
    setError(null);
    try {
      const response = await cachedFetch("/api/etf/satellite", satelliteInit(target));
      const data = await response.json();
      if (!response.ok) {
        setError(data.errors?.join(", ") ?? "조회에 실패했습니다");
        return;
      }
      setGroups(data.groups ?? []);
      setAsOf(data.as_of ?? null);
      setNotes(data.notes ?? []);
    } catch {
      setError("조회에 실패했습니다");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(country);
  }, [load, country]);

  if (loading) return <p className="py-8 text-sm text-slate-500">불러오는 중…</p>;
  if (error) {
    return (
      <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-950 dark:text-rose-300">
        {error}
      </p>
    );
  }

  const current = groups.find((g) => g.group === active) ?? groups[0];
  const firstRow = current?.subGroups[0]?.rows[0] ?? current?.excluded[0];
  const warnings = firstRow ? parseWarnings(firstRow.rationale_data) : [];
  const age = daysSince(asOf);

  return (
    <div>
      {notes.map((note) => (
        <p key={note} className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          {note}
        </p>
      ))}

      <div className="mb-2 flex gap-2">
        {groups.map((g) => (
          <button
            key={g.group}
            type="button"
            onClick={() => setActive(g.group)}
            className={`min-h-10 rounded-full border px-3 text-xs sm:min-h-0 sm:py-1 ${
              g.group === current?.group
                ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900"
                : "border-slate-300 text-slate-600 dark:border-slate-700 dark:text-slate-300"
            }`}
          >
            {g.group} <span className="opacity-60">{g.total}</span>
          </button>
        ))}
        <span className="ml-auto self-center text-xs text-slate-400">
          기준일 {asOf ?? "-"}
          {age !== null && age > 0 ? ` (${age}일 전)` : ""}
        </span>
      </div>

      {/* 경고를 목록보다 먼저. 배치가 저장한 문구 그대로다 */}
      {warnings.length > 0 ? (
        <ul className="mb-3 space-y-1 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          {warnings.map((w) => (
            <li key={w}>· {w}</li>
          ))}
        </ul>
      ) : null}

      {current && current.subGroups.length === 0 ? (
        <p className="py-4 text-sm text-slate-500">이 묶음에서 조건을 통과한 ETF 가 없습니다.</p>
      ) : null}

      {current?.subGroups.map((sub) => (
        <section key={sub.name} className="mb-4">
          <h3 className="mb-1 text-xs text-slate-500">
            {sub.name}
            <span className="ml-1 text-slate-400">
              {sub.total > sub.rows.length ? `· ${sub.total}개 중 상위 ${sub.rows.length}개` : `· ${sub.total}개`}
            </span>
          </h3>
          <div className="flex flex-col gap-2">
            {sub.rows.map((row) => (
              <SatelliteCard key={row.etf_id} row={row} />
            ))}
          </div>
        </section>
      ))}

      {current && current.excluded.length > 0 ? (
        <details className="mb-6 rounded-lg border border-slate-200 text-xs dark:border-slate-800">
          <summary className="cursor-pointer select-none px-3 py-2 text-slate-600 dark:text-slate-300">
            {current.group} 중 뺀 것 <span className="ml-1 text-slate-400">순자산 큰 순</span>
          </summary>
          <ul className="divide-y divide-slate-100 border-t border-slate-200 dark:divide-slate-800 dark:border-slate-800">
            {current.excluded.map((row) => (
              <li key={row.etf_id} className="px-3 py-2">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium">{row.country === "KR" ? row.name : row.symbol}</span>
                  <span className="text-slate-500">{row.sub_group}</span>
                  <span className="ml-auto text-slate-500">{formatAssets(row.total_assets, row.country)}</span>
                </div>
                <p className="mt-0.5 text-amber-700 dark:text-amber-300">{row.excluded_reason}</p>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function SatelliteCard({ row }: { row: SatelliteRow }) {
  const [open, setOpen] = useState(false);
  const criteria = parseCriteria(row.rationale_data);
  const kr = row.country === "KR";
  return (
    <article className="rounded-xl border border-slate-200 dark:border-slate-800">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="w-full px-3 py-2.5 text-left">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-semibold">{kr ? row.name : row.symbol}</span>
          <span className="text-xs text-slate-500">{kr ? row.symbol : row.name}</span>
          <span className="ml-auto text-sm">
            {kr ? (
              <span className="text-xs text-amber-700 dark:text-amber-300">보수 미반영</span>
            ) : (
              <>
                보수 <b>{formatPct(row.expense_ratio)}</b>
              </>
            )}
            <span className="ml-2 text-xs text-slate-400">{open ? "▲" : "▼"}</span>
          </span>
        </div>
        <div className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-slate-500">
          <span>
            {row.group_size === 1 ? "묶음 안 통과 1개" : `묶음 안 ${row.rank_in_group}/${row.group_size}위`}
          </span>
          <span>· 순자산 {formatAssets(row.total_assets, row.country)}</span>
          {kr && row.premium_abs_avg !== null ? <span>· 평균 괴리율 {formatPct(row.premium_abs_avg, 3)}</span> : null}
          {row.last_close !== null && row.last_close !== undefined ? (
            // 이은 ETF 는 일일 배치가 날마다 종가를 받는다 (25.896)
            <span>· 현재가 {money(row.last_close, row.currency ?? (row.country === "US" ? "USD" : "KRW"))} ({row.last_close_date} 종가)</span>
          ) : row.prev_close !== null && row.prev_close !== undefined ? (
            // 아직 날마다 종가가 없으면 월 판정 때 받은 직전 종가 — 현재가로 읽히지 않게 날짜를 붙인다 (25.894)
            // 미국은 야후 previousClose 라 판정일(UTC 실행일)의 전 거래일 종가다 — 판정일을 종가 날짜처럼 적었다 (25.921)
            <span>· {row.country === "US" ? "판정 전 거래일 종가" : "판정 때 종가"} {money(row.prev_close, row.currency ?? (row.country === "US" ? "USD" : "KRW"))} ({row.country === "US" ? `${row.as_of_date} 판정` : row.as_of_date})</span>
          ) : null}
        </div>
        <p className={`mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-300 ${open ? "" : "line-clamp-2"}`}>
          {row.rationale_text}
        </p>
      </button>
      {/* 펼쳤으면 0줄이어도 그린다 — 그때는 빨간 경고가 뜬다 (docs/infra.md 25.71·25.174) */}
      {open ? (
        <div className="border-t border-slate-100 px-3 pb-3 pt-1 dark:border-slate-800">
          {/* 그 자리에서 매매 입력 (25.896). 이은 ETF 만 — 잇기 전이면 다음 일일 배치가 잇는다 */}
          <div className="mb-1 flex flex-wrap items-center justify-end gap-3 text-xs">
            {row.stock_id ? (
              <QuickTrade
                stock={{
                  id: row.stock_id,
                  ticker: row.symbol,
                  name: row.name,
                  country: row.country,
                  currency: row.currency ?? (row.country === "US" ? "USD" : "KRW"),
                  market: "ETF",
                  asset_type: "etf",
                }}
                horizon="long"
              />
            ) : (
              <span className="text-slate-500">매매 입력은 다음 일일 배치가 이 ETF 를 이은 뒤 열립니다</span>
            )}
          </div>
          <CriteriaTable
            rows={criteria}
            caption={`판정일 ${row.as_of_date}`}
            docPath="docs/etf.md 10장"
            staleAfterDays={ETF_STALE_AFTER_DAYS}
          />
        </div>
      ) : null}
    </article>
  );
}
