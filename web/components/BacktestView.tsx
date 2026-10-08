"use client";

import { useCallback, useEffect, useState } from "react";
import EquityChart, { type Series } from "@/components/EquityChart";
import MarketTabs from "@/components/MarketTabs";
import {
  WHAT_OPTIONS, costDefaultsNote, costLine, dividendNote, awaitingRunner, isRunning, recentlyFinished, stuckRunNote, mergeWarnings, readStressJson, metricNumber, progressText, strategyKind, strategyLabel,
  type BacktestRun, type BatchRunRow, type CurvePoint, type StressRun, type StressWindow,
  exposureText,
  luckText, groupTags, staleVersionNote,
  stressVersionNote,
} from "@/lib/backtest";
import { readJson } from "@/lib/http";
import { readSavedCountry, saveCountry, userTimeOf, type Country } from "@/lib/market";
import { pct, pctClass, sizePct } from "@/lib/portfolio";

/**
 * 백테스트·스트레스 화면 (docs/backtest.md, docs/stress.md, Step 16).
 *
 * 화면이 지키는 것
 *   1. **경고가 맨 위다.** 수익률보다 먼저 본다. 지금 데이터에는 생존편향이 항상 있어
 *      여기 나온 숫자는 실제보다 좋다 (docs/backtest.md 4.3)
 *   2. **화면은 계산하지 않는다.** 표의 모든 숫자는 backtest_runs·stress_runs 에 저장된 값이다
 *   3. 실행은 Actions 를 깨우고 batch_runs 상태만 폴링한다. 결과는 끝난 뒤에 읽는다
 *
 * "이 가중치를 설정에 반영" 버튼은 두지 않았다. 백테스트가 팩터 가중치를 고정
 * 20% 씩으로 돌리므로(batch/jobs/backtest.run) 반영할 가중치가 나오지 않는다.
 * 가중치를 찾아 주는 기능은 파라미터 최적화이고, docs/backtest.md 5장이 하지 않기로 한 것이다.
 */

interface Data {
  market: string;
  group_id: string | null;
  runs: BacktestRun[];
  curves: CurvePoint[];
  curve_runs: Array<{ run_id: number; strategy: string }>;
  groups: Array<{ group_id: string; market: string; strategies: number; start_date: string; end_date: string; top_n: number; created_at: string; no_pit?: number | null; trend_on?: number | null; has_composite?: number | null }>;
  stress: StressRun | null;
  /** 마지막 스트레스 실행이 신호 0건이라 돌지 않았으면 그 말 (25.825) */
  stress_note?: string | null;
  basket_stocks: Array<{ id: number; ticker: string; name: string }>;
  status: BatchRunRow[];
  notice: string | null;
}

const LINE_COLORS = ["#2563eb", "#64748b", "#16a34a"];

/** 기본으로 겹쳐 보는 선. 종합과 벤치마크를 나란히 두면 "규칙이 시장을 이겼나" 가 바로 보인다 */
const DEFAULT_CURVES = ["composite", "benchmark"];

export default function BacktestView() {
  const [country, setCountry] = useState<Country>("KR");
  const [data, setData] = useState<Data | null>(null);
  const [curveKeys, setCurveKeys] = useState<string[]>(DEFAULT_CURVES);
  const [group, setGroup] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    setCountry(readSavedCountry());
  }, []);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ market: country, curves: curveKeys.join(",") });
      if (group) params.set("group", group);
      const read = await readJson<Data>(`/api/backtest?${params.toString()}`);
      if (!read.ok) throw new Error(read.error ?? "불러오지 못했습니다");
      setData(read.data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "불러오지 못했습니다");
    }
  }, [country, curveKeys, group]);

  useEffect(() => {
    void load();
  }, [load]);

  // 고른 전략이 이 묶음에 없으면(장기 문턱 비교 묶음 등) 서버가 대신 고른 것을 따라간다.
  // 그래야 차트와 아래 단추의 선택 표시가 어긋나지 않는다
  useEffect(() => {
    if (!data || data.runs.length === 0) return;
    if (data.runs.some((r) => curveKeys.includes(r.strategy))) return;
    setCurveKeys(data.curve_runs.map((c) => c.strategy));
  }, [data, curveKeys]);

  // 도는 중일 때만 폴링한다. 백테스트는 몇 분 걸린다 (docs/backtest.md 8장)
  const running = data ? isRunning(data.status) : false;
  const refresh = useCallback(() => void load(), [load]);
  // 방금 끝난 단계 뒤에 다음 단계(스트레스)가 열릴 수 있어 2분은 더 읽는다 (25.589)
  const settling = data ? recentlyFinished(data.status) : false;
  useEffect(() => {
    if (!running && !settling) return;
    // 화면이 뒤에 있으면 읽지 않는다 — DB 읽기 절약 (25.1032)
    const timer = setInterval(() => document.visibilityState !== "hidden" && void load(), 20_000);
    return () => clearInterval(timer);
  }, [running, settling, load]);

  const changeCountry = (next: Country) => {
    setCountry(next);
    saveCountry(next);
    setGroup(null);
  };

  const runs = data?.runs ?? [];
  const warnings = mergeWarnings(runs);
  const bench = runs.find((r) => r.strategy === "benchmark") ?? null;

  return (
    <div>
      <MarketTabs active={country} onChange={changeCountry} />

      {error ? <Band tone="error">{error}</Band> : null}
      {data?.notice ? <Band tone="warn">{data.notice}</Band> : null}
      {notice ? <Band tone="ok">{notice}</Band> : null}
      {data && stuckRunNote(data.status) ? <Band tone="warn">{stuckRunNote(data.status)}</Band> : null}

      {/* 경고가 먼저다. 수익률보다 위에 둔다 (docs/backtest.md 4.3) */}
      {warnings.length > 0 ? (
        <section className="mb-3 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          <p className="font-semibold">이 결과를 읽기 전에</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
          <p className="mt-1 opacity-80">경고가 붙은 결과로 규칙을 확정하지 않습니다.</p>
        </section>
      ) : null}

      <RunForm market={country} running={running || settling} status={data?.status ?? []} onRequested={(m) => { setNotice(m); void load(); }} onRefresh={refresh} />

      {runs.length === 0 ? (
        <p className="rounded-xl border border-slate-200 px-3 py-6 text-center text-sm text-slate-500 dark:border-slate-800">
          아직 {country === "KR" ? "국내" : "미국"} 백테스트 결과가 없습니다. 위에서 실행을 요청하거나 GitHub Actions 에서 직접 돌리세요.
        </p>
      ) : (
        <>
          <RunHeader runs={runs} groups={data?.groups ?? []} active={data?.group_id ?? null} onPick={setGroup} />
          <ResultTable runs={runs} bench={bench} />
          <CurvePicker runs={runs} chosen={curveKeys} onChange={setCurveKeys} />
          <EquityChart series={toSeries(data)} />
          <h3 className="mb-1 mt-3 text-xs font-semibold text-slate-600 dark:text-slate-300">낙폭 (고점 대비)</h3>
          {toDrawdownSeries(data).some((s) => s.points.length > 0) ? (
            <EquityChart series={toDrawdownSeries(data)} height={160} format="pct" />
          ) : (
            <p className="text-xs text-slate-500">이 실행은 낙폭을 저장하기 전 것입니다. 다시 돌리면 나옵니다.</p>
          )}
        </>
      )}

      <Stress data={data} />
    </div>
  );
}

function toSeries(data: Data | null): Series[] {
  if (!data) return [];
  const byRun = new Map<number, Array<{ date: string; value: number }>>();
  for (const p of data.curves) {
    const list = byRun.get(p.run_id) ?? [];
    list.push({ date: p.date, value: p.equity });
    byRun.set(p.run_id, list);
  }
  return data.curve_runs.map((cr, i) => ({
    label: strategyLabel(cr.strategy),
    color: LINE_COLORS[i % LINE_COLORS.length],
    points: byRun.get(cr.run_id) ?? [],
  }));
}

function toDrawdownSeries(data: Data | null): Series[] {
  if (!data) return [];
  const byRun = new Map<number, Array<{ date: string; value: number }>>();
  for (const p of data.curves) {
    if (p.drawdown === null || p.drawdown === undefined) continue;  // 0032 이전 실행
    const list = byRun.get(p.run_id) ?? [];
    list.push({ date: p.date, value: p.drawdown });
    byRun.set(p.run_id, list);
  }
  return data.curve_runs.map((cr, i) => ({
    label: strategyLabel(cr.strategy),
    color: LINE_COLORS[i % LINE_COLORS.length],
    points: byRun.get(cr.run_id) ?? [],
  }));
}

function Band({ tone, children }: { tone: "error" | "warn" | "ok"; children: React.ReactNode }) {
  const style = {
    error: "bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
    warn: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
    ok: "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  }[tone];
  return <p className={`mb-2 rounded-lg px-3 py-2 text-xs ${style}`}>{children}</p>;
}

// ----------------------------------------------------------------------
// 실행 요청
// ----------------------------------------------------------------------

function RunForm({
  market, running, status, onRequested, onRefresh,
}: {
  market: Country; running: boolean; status: BatchRunRow[]; onRequested: (message: string) => void; onRefresh: () => void;
}) {
  const [what, setWhat] = useState<(typeof WHAT_OPTIONS)[number]>("둘다");
  const [years, setYears] = useState(5);
  const [topN, setTopN] = useState(20);
  const [trendFilter, setTrendFilter] = useState(false);
  const [noPit, setNoPit] = useState(false);
  const [busy, setBusy] = useState(false);
  // 요청 뒤 실행 기록이 생길 때까지 잠근다 (25.585). 그동안 20초마다 상태를 다시 읽는다 — 부모 폴링은 running 행이 있어야 돈다
  const [requestedAt, setRequestedAt] = useState<number | null>(null);
  const [, setTick] = useState(0);
  const waiting = awaitingRunner(requestedAt, status);
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => {
      setTick((t) => t + 1);
      if (document.visibilityState !== "hidden") onRefresh();
    }, 20_000);
    return () => clearInterval(timer);
  }, [waiting, onRefresh]);
  const last = status[0];

  const submit = async () => {
    setBusy(true);
    try {
      const res = await fetch("/api/backtest/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ market, what, years, top_n: topN, trend_filter: trendFilter, no_pit_universe: noPit }),
      });
      const json = await res.json();
      if (json.dispatched) setRequestedAt(Date.now());
      onRequested(
        json.dispatched
          ? "실행을 요청했습니다. 몇 분 걸립니다. 아래 상태가 스스로 새로고침됩니다"
          : `깨우지 못했습니다: ${json.reason ?? json.errors?.join(", ") ?? "알 수 없는 이유"}`,
      );
    } catch (e) {
      onRequested(e instanceof Error ? e.message : "요청에 실패했습니다");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mb-3 rounded-xl border border-slate-200 px-3 py-2.5 dark:border-slate-800">
      <div className="flex flex-wrap items-end gap-2 text-xs">
        <Field label="무엇을">
          <select value={what} onChange={(e) => setWhat(e.target.value as (typeof WHAT_OPTIONS)[number])} className={INPUT}>
            {WHAT_OPTIONS.map((o) => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>
        </Field>
        <Field label="기간(년)">
          <input type="number" min={1} max={20} value={years} onChange={(e) => setYears(Number(e.target.value))} className={`${INPUT} w-16`} />
        </Field>
        <Field label="매월 종목 수">
          <input type="number" min={5} max={100} value={topN} onChange={(e) => setTopN(Number(e.target.value))} className={`${INPUT} w-16`} />
        </Field>
        <label className="flex items-center gap-1.5 pb-1.5 text-slate-600 dark:text-slate-300" title="지수가 200일선 아래면 비중 × 설정 배수">
          <input type="checkbox" checked={trendFilter} onChange={(e) => setTrendFilter(e.target.checked)} />
          추세 필터
        </label>
        <label className="flex items-center gap-1.5 pb-1.5 text-slate-600 dark:text-slate-300" title="비교용. 오늘 유니버스를 전 구간에 쓴다">
          <input type="checkbox" checked={noPit} onChange={(e) => setNoPit(e.target.checked)} />
          시점 유니버스 끄기
        </label>
        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy || running || waiting}
          className="h-11 rounded-lg bg-slate-900 px-4 text-white disabled:opacity-50 sm:h-auto sm:px-3 sm:py-1.5 dark:bg-slate-100 dark:text-slate-900"
        >
          {running ? "실행 중" : waiting ? "러너 준비 중" : busy ? "요청 중..." : "실행 요청"}
        </button>
      </div>
      <p className="mt-1.5 text-[11px] text-slate-500">
        {last
          ? `최근 실행: ${last.job_name} ${last.market ?? ""} ${statusLabel(last.status)} (${userTimeOf(last.started_at)}${last.finished_at ? ` → ${userTimeOf(last.finished_at).slice(11)}` : ""} KST)`
          : "실행 기록이 없습니다"}
        {last?.error_text ? ` · ${last.error_text}` : ""}
        {progressText(last) ? ` · 진행 ${progressText(last)}` : ""}
      </p>
      <p className="text-[11px] text-slate-400">
        종목 수는 기본 20 에서 거의 바꾸지 않습니다. 과거에 맞춰 고르면 과최적화입니다.
      </p>
    </section>
  );
}

/** 입력칸: 폰에서 손가락 높이(44px). 넓은 화면은 전처럼 낮게 (docs/pwa.md 3.2) */
const INPUT = "h-11 rounded-lg border border-slate-300 bg-transparent px-2 sm:h-auto sm:py-1 dark:border-slate-700";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-[11px] text-slate-500">{label}</span>
      {children}
    </label>
  );
}

function statusLabel(status: string): string {
  return { running: "실행 중", success: "성공", partial: "일부 성공", failed: "실패", skipped: "건너뜀" }[status] ?? status;
}

// ----------------------------------------------------------------------
// 결과
// ----------------------------------------------------------------------

function RunHeader({
  runs, groups, active, onPick,
}: {
  runs: BacktestRun[];
  groups: Data["groups"];
  active: string | null;
  onPick: (group: string) => void;
}) {
  const first = runs[0];
  return (
    <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs text-slate-500">
      <span>
        {first.start_date} ~ {first.end_date} · 매월 상위 {first.top_n} · 리밸런스 {first.rebalances}회
      </span>
      {/* 어떤 비용을 썼는지 그대로 보여 준다. 기본값인 항목에만 그렇다고 적는다 (docs/backtest.md 3장, infra 25.358) */}
      <span>비용 {costLine(first.costs_json)} {costDefaultsNote(first.costs_json)}</span>
      <span title="엔진 판 / 점수 계산식 판. 판이 다르면 두 실행의 수익률을 나란히 놓고 비교할 수 없습니다">
        계산 판 엔진 {first.calc_version} · 점수{" "}
        {first.scoring_calc_version === null ? "기록 없음" : first.scoring_calc_version}
      </span>
      {staleVersionNote(first) ? (
        <span className="w-full rounded bg-amber-50 px-2 py-1 text-amber-800 dark:bg-amber-950 dark:text-amber-200">{staleVersionNote(first)}</span>
      ) : null}
      {groups.length > 1 ? (
        <select value={active ?? ""} onChange={(e) => onPick(e.target.value)} className={`${INPUT} text-[11px]`}>
          {/* 보이는 묶음이 목록(최근 12개) 밖이면 맨 위에 넣는다 — 없으면 select 가 다른 묶음을 가리킨다 (25.790, 교차검증) */}
          {active && !groups.some((g) => g.group_id === active) ? (
            <option value={active}>{first.start_date}~{first.end_date} · 상위 {first.top_n} · 기본 묶음</option>
          ) : null}
          {groups.map((g) => (
            <option key={g.group_id} value={g.group_id}>
              {/* 기간·종목 수까지 적는다 — 이름만으로 어느 조건의 묶음인지 알 수 있게 (25.581) */}
              {userTimeOf(g.created_at)} · {g.start_date}~{g.end_date} · 상위 {g.top_n} · {g.strategies}전략{groupTags(g)}
            </option>
          ))}
        </select>
      ) : null}
    </div>
  );
}

function ResultTable({ runs, bench }: { runs: BacktestRun[]; bench: BacktestRun | null }) {
  const order = { main: 0, long: 1, factor: 2, candidate: 3 };
  const sorted = [...runs].sort((a, b) => order[strategyKind(a.strategy)] - order[strategyKind(b.strategy)]);
  return (
    <>
    {/* 폰: 전략마다 카드. 열 칸 표는 폰에서 옆으로 넘쳤다 (docs/pwa.md 3.2, 5단계). 값은 표와 같다 */}
    <ul className="mb-3 flex flex-col gap-2 text-xs sm:hidden">
      {sorted.map((r) => {
        const m = safeJson(r.metrics_json);
        const isBench = r.strategy === "benchmark";
        const cells: Array<[string, string]> = [
          ["최종 자산", r.final_equity.toFixed(3)],
          ["CAGR", pct(metricNumber(m, "cagr"))],
          ["MDD", pct(metricNumber(m, "mdd"))],
          ["샤프", fixed(metricNumber(m, "sharpe"))],
          ["소르티노", fixed(metricNumber(m, "sortino"))],
          ["변동성", sizePct(metricNumber(m, "volatility_ann"))],
          ["초과 CAGR", isBench ? "기준" : pct(r.excess_cagr)],
          ["이긴 달", isBench ? "-" : sizePct(r.win_rate, 0)],
          ["회전율", r.turnover_avg === null ? "-" : `${(r.turnover_avg * 100).toFixed(0)}%`],
          ["투자한 달", exposureText(m).invested],
          ["평균 보유", exposureText(m).holdings],
          // 첫 투자일은 폰에서도 보인다(표에서는 칸의 title) — 25.789, 교차검증
          ["첫 투자", exposureText(m).first ?? "-"],
          // 운인가 실력인가 (25.997)
          ["우연일 확률", isBench ? "기준" : luckText(m).cell],
        ];
        return (
          <li key={r.id} className={`rounded-xl border border-slate-200 px-3 py-2 dark:border-slate-800 ${isBench ? "bg-slate-50 dark:bg-slate-900" : ""}`}>
            <p className="font-medium">
              {strategyLabel(r.strategy)}
              {strategyKind(r.strategy) === "factor" ? <span className="ml-1 text-[10px] text-slate-400">성과요인</span> : strategyKind(r.strategy) === "candidate" ? <span className="ml-1 text-[10px] text-amber-600">후보 · 추천에 안 씀</span> : null}
            </p>
            <dl className="mt-1 grid grid-cols-3 gap-x-3 gap-y-0.5">
              {cells.map(([k, v]) => (
                <div key={k} className="flex justify-between gap-1">
                  <dt className="text-slate-400">{k}</dt>
                  <dd className="tabular-nums">{v}</dd>
                </div>
              ))}
            </dl>
          </li>
        );
      })}
      <li className="px-1 text-[11px] text-slate-500">
        초과 CAGR·이긴 달은 벤치마크({bench ? strategyLabel(bench.strategy) : "없음"}) 대비입니다. {dividendNote(runs[0]?.market ?? "KR")}
      </li>
    </ul>
    <div className="mb-3 hidden overflow-x-auto rounded-xl border border-slate-200 text-xs sm:block dark:border-slate-800">
      <table className="w-full">
        <thead className="bg-slate-50 text-left text-slate-500 dark:bg-slate-900">
          <tr>
            <th className="px-3 py-1.5 font-medium">전략</th>
            <th className="px-3 py-1.5 font-medium">최종 자산</th>
            <th className="px-3 py-1.5 font-medium">CAGR</th>
            <th className="px-3 py-1.5 font-medium">MDD</th>
            <th className="px-3 py-1.5 font-medium">샤프</th>
            <th className="px-3 py-1.5 font-medium">소르티노</th>
            <th className="px-3 py-1.5 font-medium">변동성</th>
            <th className="px-3 py-1.5 font-medium">초과 CAGR</th>
            <th className="px-3 py-1.5 font-medium">이긴 달</th>
            <th className="px-3 py-1.5 font-medium">회전율</th>
            <th className="px-3 py-1.5 font-medium">투자한 달</th>
            <th className="px-3 py-1.5 font-medium">평균 보유</th>
            <th className="px-3 py-1.5 font-medium" title="이 초과수익이 우연일 확률 — 디플레이티드 샤프 비율(시험한 전략 수를 감안)">우연일 확률</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => {
            const m = safeJson(r.metrics_json);
            const isBench = r.strategy === "benchmark";
            return (
              <tr key={r.id} className={`border-t border-slate-100 dark:border-slate-800 ${isBench ? "bg-slate-50 dark:bg-slate-900" : ""}`}>
                <td className="whitespace-nowrap px-3 py-1.5 font-medium">
                  {strategyLabel(r.strategy)}
                  {strategyKind(r.strategy) === "factor" ? <span className="ml-1 text-[10px] text-slate-400">성과요인</span> : strategyKind(r.strategy) === "candidate" ? <span className="ml-1 text-[10px] text-amber-600">후보 · 추천에 안 씀</span> : null}
                </td>
                <td className="px-3 py-1.5">{r.final_equity.toFixed(3)}</td>
                <td className="px-3 py-1.5">{pct(metricNumber(m, "cagr"))}</td>
                <td className="px-3 py-1.5">{pct(metricNumber(m, "mdd"))}</td>
                <td className="px-3 py-1.5">{fixed(metricNumber(m, "sharpe"))}</td>
                <td className="px-3 py-1.5">{fixed(metricNumber(m, "sortino"))}</td>
                <td className="px-3 py-1.5">{sizePct(metricNumber(m, "volatility_ann"))}</td>
                <td className="px-3 py-1.5">{isBench ? "기준" : pct(r.excess_cagr)}</td>
                <td className="px-3 py-1.5">{isBench ? "-" : sizePct(r.win_rate, 0)}</td>
                <td className="px-3 py-1.5">{r.turnover_avg === null ? "-" : `${(r.turnover_avg * 100).toFixed(0)}%`}</td>
                <td className="px-3 py-1.5" title={exposureText(m).first ? `첫 투자 ${exposureText(m).first}` : undefined}>{exposureText(m).invested}</td>
                <td className="px-3 py-1.5">{exposureText(m).holdings}</td>
                <td className="px-3 py-1.5" title={luckText(m).title ?? undefined}>{isBench ? "기준" : luckText(m).cell}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="px-3 py-1.5 text-[11px] text-slate-500">
        초과 CAGR·이긴 달은 벤치마크({bench ? strategyLabel(bench.strategy) : "없음"}) 대비입니다. {dividendNote(runs[0]?.market ?? "KR")}
        팩터 하나짜리 줄이 성과요인분석입니다. 투자한 달이 절반 미만이거나 평균 보유가 3종목 미만이면 그 수익은 표본이 적어 믿지 않습니다.
        우연일 확률은 벤치마크 대비 월 초과수익의 샤프를, 같은 실행에서 시험한 전략 수만큼 높인 문턱과 견준 값입니다(디플레이티드 샤프 비율) — 낮을수록 실력에 가깝습니다. 24개월 미만이면 판정하지 않습니다.
      </p>
    </div>
    </>
  );
}

function fixed(value: number | null): string {
  return value === null ? "-" : value.toFixed(2);
}

function CurvePicker({
  runs, chosen, onChange,
}: {
  runs: BacktestRun[]; chosen: string[]; onChange: (next: string[]) => void;
}) {
  const toggle = (strategy: string) => {
    if (chosen.includes(strategy)) {
      onChange(chosen.filter((s) => s !== strategy));
      return;
    }
    // 한 번에 세 개까지. 더 겹치면 선이 뭉쳐 읽히지 않고 응답도 무거워진다
    onChange([...chosen, strategy].slice(-3));
  };
  return (
    <div className="mb-1 flex flex-wrap gap-1">
      {runs.map((r) => (
        <button
          key={r.id}
          type="button"
          onClick={() => toggle(r.strategy)}
          className={`min-h-9 rounded-full border px-3 text-xs sm:min-h-0 sm:px-2 sm:py-0.5 sm:text-[11px] ${
            chosen.includes(r.strategy)
              ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900"
              : "border-slate-300 text-slate-600 dark:border-slate-700 dark:text-slate-300"
          }`}
        >
          {strategyLabel(r.strategy)}
        </button>
      ))}
    </div>
  );
}

// ----------------------------------------------------------------------
// 스트레스 (docs/stress.md)
// ----------------------------------------------------------------------

function Stress({ data }: { data: Data | null }) {
  const stress = data?.stress ?? null;
  if (!stress) {
    return (
      <section className="mt-4">
        <h2 className="mb-1 text-sm font-semibold">스트레스 테스트</h2>
        <p className="rounded-xl border border-slate-200 px-3 py-4 text-sm text-slate-500 dark:border-slate-800">
          아직 결과가 없습니다. 지금 추천된 바스켓이 과거 최악의 구간에서 얼마나 빠졌는지 봅니다.
        </p>
      </section>
    );
  }
  // 모양까지 확인해 읽는다 — 깨진 칸 하나가 화면 전체를 죽이지 않게 (25.581)
  const { windows, warnings: 저장된_경고, basket, excluded, skipped, unreadable } = readStressJson(stress);
  const warnings = unreadable.length
    ? [`저장된 ${unreadable.join("·")} 을(를) 읽지 못했습니다 — 그 부분은 비어 보입니다`, ...저장된_경고]
    : 저장된_경고;
  const nameOf = new Map(data?.basket_stocks.map((s) => [s.id, `${s.name}(${s.ticker})`]) ?? []);

  return (
    <section className="mt-4">
      <h2 className="mb-1 text-sm font-semibold">스트레스 테스트</h2>
      {stressVersionNote(Number(stress.calc_version)) ? (
        <p className="mb-2 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          {stressVersionNote(Number(stress.calc_version))}
        </p>
      ) : null}
      {data?.stress_note ? (
        <p className="mb-2 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          {data.stress_note}
        </p>
      ) : null}
      {warnings.length > 0 ? (
        <ul className="mb-2 list-disc space-y-0.5 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 pl-7 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
      {/* 폰: 창마다 카드. 다섯 칸 표는 날짜 구간이 길어 옆으로 넘쳤다 */}
      <ul className="mb-1 flex flex-col gap-2 text-xs sm:hidden">
        {windows.map((w) => (
          <li key={w.length} className="rounded-xl border border-slate-200 px-3 py-2 dark:border-slate-800">
            <p className="font-medium">
              {w.length}거래일 <span className="font-normal text-slate-500">{w.start} ~ {w.end}</span>
            </p>
            <p className="mt-0.5">
              수익률 <span className={pctClass(w.return_pct)}>{pct(w.return_pct)}</span>
              {" "}· 창 안 최대 낙폭 {pct(w.max_drawdown)}
              {" "}· 회복 {w.recovery_days === null ? "아직 못 함" : `${w.recovery_days}거래일`}
            </p>
          </li>
        ))}
      </ul>
      <div className="overflow-x-auto rounded-xl border border-slate-200 text-xs dark:border-slate-800">
        <table className="hidden w-full sm:table">
          <thead className="bg-slate-50 text-left text-slate-500 dark:bg-slate-900">
            <tr>
              <th className="px-3 py-1.5 font-medium">창</th>
              <th className="px-3 py-1.5 font-medium">최악 구간</th>
              <th className="px-3 py-1.5 font-medium">수익률</th>
              <th className="px-3 py-1.5 font-medium">창 안 최대 낙폭</th>
              <th className="px-3 py-1.5 font-medium">회복</th>
            </tr>
          </thead>
          <tbody>
            {windows.map((w) => (
              <tr key={w.length} className="border-t border-slate-100 dark:border-slate-800">
                <td className="whitespace-nowrap px-3 py-1.5 font-medium">{w.length}거래일</td>
                <td className="whitespace-nowrap px-3 py-1.5">{w.start} ~ {w.end}</td>
                <td className={`px-3 py-1.5 ${pctClass(w.return_pct)}`}>{pct(w.return_pct)}</td>
                <td className="px-3 py-1.5">{pct(w.max_drawdown)}</td>
                <td className="px-3 py-1.5">{w.recovery_days === null ? "아직 회복 못 함" : `${w.recovery_days}거래일`}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="px-3 py-1.5 text-[11px] text-slate-500">
          기준일 {stress.as_of_date} 바스켓 {Object.keys(basket).length}종목 ·
          비중 {stress.weighting === "suggested" ? "권장 비중" : "동일가중"} · 현금 {(stress.cash_weight * 100).toFixed(0)}% ·
          가격 {stress.curve_start}~{stress.curve_end}
          {excluded.length > 0 ? ` · 시작일 가격이 없어 뺀 종목 ${excluded.length}개` : ""}
          {skipped.length > 0 ? ` · 표본이 모자라 못 본 창 ${skipped.join("·")}일` : ""}
        </p>
      </div>
      <details className="mt-1 text-xs">
        <summary className="cursor-pointer select-none text-slate-500">바스켓 보기 ({Object.keys(basket).length}종목)</summary>
        <p className="mt-1 leading-relaxed text-slate-600 dark:text-slate-300">
          {Object.entries(basket)
            .sort((a, b) => b[1] - a[1])
            .map(([id, weight]) => `${nameOf.get(Number(id)) ?? id} ${(weight * 100).toFixed(1)}%`)
            .join(" · ")}
        </p>
      </details>
    </section>
  );
}

function safeJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}
