"use client";

import Link from "next/link";
import { afterHydration, cachedFetch, markHydrated, peekCachedJson } from "@/lib/clientCache";
import { useCallback, useEffect, useRef, useState } from "react";
import CriteriaTable from "@/components/CriteriaTable";
import MarketTabs from "@/components/MarketTabs";
import {
  HORIZON_LABEL,
  HORIZON_ORDER,
  STALE_AFTER_DAYS,
  daysSince,
  formatKst,
  groupByHorizon,
  isStale,
  outcomeLine,
  parseCriteria,
  performanceLine,
  parseFactors,
  parseTranches,
  REPORT_TOP_STOCKS,
  reductionNote,
  sentimentLine,
  signalKey,
  type OutcomeStat,
  type Calibration,
  calibrationLines,
  type RecommendRow,
  type SignalKeyRow,
} from "@/lib/recommend";
import { readSavedCountry, saveCountry, userDateOf, type Country } from "@/lib/market";
import { addWatch, loadWatchIds, removeWatch } from "@/lib/watch";
import QuickTrade from "@/components/QuickTrade";

/**
 * 추천 목록.
 *
 * 한 종목마다 답해야 하는 질문이 넷이다. 그 순서로 배치한다.
 *   무엇을    종목과 점수
 *   왜        근거 문장. 저장된 수치만 인용한다
 *   얼마에    권장 매수 구간과 3회 분할
 *   얼마나    권장 금액과 비중. 줄었으면 그 이유
 *
 * 폰에서 길어지는 문제를 두 겹으로 막는다 (2026-09-16).
 *   기간 탭    단기·중기·장기를 한 번에 하나만 본다
 *   카드 접기  기본은 "무엇을 + 왜" 두 줄. 탭하면 나머지가 펼쳐진다
 */

const FACTORS: Array<[string, string]> = [
  ["value", "밸류"],
  ["quality", "퀄리티"],
  ["growth", "성장"],
  ["momentum", "모멘텀"],
  ["risk", "안정성"],
];

function money(value: number | null, currency: string): string {
  if (value === null || value === undefined) return "-";
  if (currency === "KRW") {
    return `${Math.round(value).toLocaleString("ko-KR")}원`;
  }
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function score(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : value.toFixed(0);
}

type HorizonTab = "all" | (typeof HORIZON_ORDER)[number];

interface LastRun {
  finished_at: string | null;
  market: string | null;
}

/** 추천 조회 요청. 캐시 열쇠가 (방법, 주소, 본문)이라 조회와 꺼내기가 같은 모양을 써야 한다 */
function recommendInit(country: Country): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ country }) };
}

interface RecommendResponse {
  rows?: RecommendRow[];
  notes?: string[];
  as_of?: string | null;
  last_run?: LastRun | null;
  added?: unknown;
  dropped?: unknown;
  previous_as_of?: string | null;
  outcomes?: unknown;
  calibration?: unknown;
}

/**
 * 메뉴로 옮겨 온 화면이면 저장된 나라와 **이미 읽은 응답**으로 바로 그린다 (docs/infra.md 25.893).
 * 첫 하이드레이션에서는 서버 그림과 같게 국내·빈 상태로 시작한다(예전과 같다)
 */
function 처음값(): { country: Country; data: RecommendResponse | null } {
  if (!afterHydration()) return { country: "KR", data: null };
  const country = readSavedCountry();
  return { country, data: peekCachedJson<RecommendResponse>("/api/recommend", recommendInit(country)) };
}

export default function RecommendList() {
  const [처음] = useState(처음값);
  const d0 = 처음.data;
  const [rows, setRows] = useState<RecommendRow[]>(d0?.rows ?? []);
  const [notes, setNotes] = useState<string[]>(d0?.notes ?? []);
  const [asOf, setAsOf] = useState<string | null>(d0?.as_of ?? null);
  const [lastRun, setLastRun] = useState<LastRun | null>(d0?.last_run ?? null);
  // 어제와 비교. 새로 들어온 키와 빠진 종목 (Step 24)
  const [added, setAdded] = useState<Set<string>>(
    () => new Set<string>(Array.isArray(d0?.added) ? (d0.added as string[]) : []),
  );
  const [dropped, setDropped] = useState<SignalKeyRow[]>(Array.isArray(d0?.dropped) ? (d0.dropped as SignalKeyRow[]) : []);
  const [previousAsOf, setPreviousAsOf] = useState<string | null>(d0?.previous_as_of ?? null);
  const [outcomes, setOutcomes] = useState<OutcomeStat[]>(Array.isArray(d0?.outcomes) ? (d0.outcomes as OutcomeStat[]) : []);
  // 점수 보정표 (docs/signals.md 10.1) — 성적표 작업이 남긴 것. 없으면 그리지 않는다
  const [calibration, setCalibration] = useState<Calibration[]>(Array.isArray(d0?.calibration) ? (d0.calibration as Calibration[]) : []);
  // 관심 종목 (종목 id → 관심 행 id). 카드의 등록·해제 단추가 쓴다 (Step 26)
  const [watchIds, setWatchIds] = useState<Map<number, number>>(() => new Map());
  // 못 읽으면 앞의 목록을 그대로 두고 "모름" 을 알린다 — 빈 목록으로 바꾸면 모두 "관심 등록" 으로 보였다 (25.815)
  const [watchUnknown, setWatchUnknown] = useState(false);
  const refreshWatch = useCallback(async () => {
    const ids = await loadWatchIds();
    setWatchUnknown(ids === null);
    if (ids) setWatchIds(ids);
  }, []);
  const toggleWatch = async (stockId: number) => {
    const id = watchIds.get(stockId);
    const res = id ? await removeWatch(id) : await addWatch(stockId);
    if (!res.ok) window.alert(`관심 종목 ${id ? "해제" : "등록"} 실패: ${res.error}`);
    await refreshWatch();
  };
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(d0 === null);

  // 국내·미국 탭. 처음 그릴 때는 국내로 두고, 브라우저에서 마지막 선택을 읽어 바꾼다.
  // 서버에서 그린 화면과 첫 렌더가 달라지지 않게 하려는 것이다.
  const [country, setCountry] = useState<Country>(처음.country);
  const [countryReady, setCountryReady] = useState(afterHydration());
  useEffect(() => {
    markHydrated();
    setCountry(readSavedCountry());
    setCountryReady(true);
  }, []);

  // 기간 탭. 마지막 선택은 이 브라우저에만 기억한다. 저장에 실패해도 화면은 돈다.
  const [horizon, setHorizon] = useState<HorizonTab>(() => readSavedTab());

  // **가장 늦게 보낸 조회만 싣는다** (docs/infra.md 25.586, 감사). 국내→미국→국내를 빨리 누르면 늦게 온 미국 응답이
  // 국내 탭에 미국 신호·기준일을 실었다(스크리너 25.577 과 같은 모양)
  const 조회번호 = useRef(0);
  const load = useCallback(async (target: Country) => {
    const 번호 = ++조회번호.current;
    // 이미 읽은 응답이 있으면 "불러오는 중" 을 띄우지 않는다 — 값은 다음 줄의 캐시에서 곧바로 온다 (25.893)
    if (peekCachedJson("/api/recommend", recommendInit(target)) === null) setLoading(true);
    setError(null);
    try {
      const response = await cachedFetch("/api/recommend", recommendInit(target));
      const data = await response.json();
      if (번호 !== 조회번호.current) return;
      if (!response.ok) {
        setError(data.errors?.join(", ") ?? "조회에 실패했습니다");
        // 앞 시장의 카드를 이 탭에 남기지 않는다 (25.586)
        setRows([]);
        return;
      }
      setRows(data.rows ?? []);
      setNotes(data.notes ?? []);
      setAsOf(data.as_of ?? null);
      setLastRun(data.last_run ?? null);
      setAdded(new Set<string>(Array.isArray(data.added) ? data.added : []));
      setDropped(Array.isArray(data.dropped) ? data.dropped : []);
      setPreviousAsOf(data.previous_as_of ?? null);
      setOutcomes(Array.isArray(data.outcomes) ? data.outcomes : []);
      setCalibration(Array.isArray(data.calibration) ? (data.calibration as Calibration[]) : []);
    } catch {
      if (번호 === 조회번호.current) {
        setError("조회에 실패했습니다");
        setRows([]);
      }
    } finally {
      if (번호 === 조회번호.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (countryReady) void load(country);
  }, [load, country, countryReady]);
  useEffect(() => {
    void refreshWatch();
  }, [refreshWatch]);

  const tabs = (
    <MarketTabs
      active={country}
      onChange={(next) => {
        setCountry(next);
        saveCountry(next);
      }}
    />
  );

  if (loading) {
    return (
      <div>
        {tabs}
        <p className="py-8 text-sm text-slate-500">불러오는 중…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        {tabs}
        <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-950 dark:text-rose-300">
          {error}
        </p>
      </div>
    );
  }

  // 훅이 아니다. early return 뒤에서 훅을 부르면 렌더마다 훅 개수가 달라져
  // React 가 던진다 (e49e312 에서 실제로 그렇게 만들었다가 여기서 고쳤다).
  const groups = groupByHorizon(rows);
  const counts: Record<string, number> = { all: rows.length };
  for (const g of groups) counts[g.horizon] = g.rows.length;

  // 고른 기간에 건이 없으면 전체로 돌린다. 빈 화면을 두지 않는다.
  const active: HorizonTab =
    horizon !== "all" && !counts[horizon] ? "all" : horizon;
  const visible = active === "all" ? groups : groups.filter((g) => g.horizon === active);

  return (
    <div>
      {tabs}
      <Summary
        count={rows.length}
        counts={counts}
        asOf={asOf}
        lastRun={lastRun}
      />
      {watchUnknown && (
        <p className="mb-2 text-xs text-amber-700 dark:text-amber-300">
          관심 목록을 읽지 못해 카드의 관심 등록 여부가 맞지 않을 수 있습니다
        </p>
      )}

      {outcomes.length > 0 ? (
        <details className="mb-2 rounded-lg border border-slate-200 px-3 py-2 text-xs dark:border-slate-800">
          <summary className="cursor-pointer select-none">
            지난 신호 성적 (계산 {userDateOf(outcomes[0].computed_at)}) — 그때 추천이 맞았나
          </summary>
          <ul className="mt-1 space-y-0.5 text-slate-600 dark:text-slate-300">
            {outcomes.map((o) => (
              <li key={`${o.horizon}-${o.window_days}`}>{outcomeLine(o)}</li>
            ))}
          </ul>
          <p className="mt-1 text-slate-400">
            진입은 기준일 이후 첫 거래일 종가, 터치는 종가 기준입니다. 수수료·세금·배당을 반영하지 않은 가격 수익률이고,
            같은 종목의 연속 신호도 각각 셉니다. 표본이 적을수록 우연입니다.
          </p>
          {calibration.length > 0 && (
            <div className="mt-2 border-t border-slate-100 pt-2 dark:border-slate-800">
              {/* 점수 보정표 (docs/signals.md 10.1, infra 25.949): 종합 점수가 높을수록 실제 수익도 높았나. 배치가 셌고 여기는 읽기만 */}
              <p className="font-medium">점수 보정표 — 종합 점수는 실제로 몇 % 였나</p>
              <ul className="mt-0.5 space-y-0.5 text-slate-600 dark:text-slate-300">
                {calibration.map((c) =>
                  calibrationLines(c).map((line, i) => (
                    <li key={`${c.window}-${i}`} className={i === 0 ? "" : "pl-3 text-slate-500"}>
                      {line}
                    </li>
                  )),
                )}
              </ul>
              <p className="mt-1 text-slate-400">
                신호 날의 종합 점수와 그 뒤 수익률을 짝지었습니다. 같은 종목·같은 날의 기간별 신호는 한 짝입니다. 표본 30건부터 순위 상관을 냅니다.
              </p>
            </div>
          )}
        </details>
      ) : null}

      {previousAsOf ? (
        <details className="mb-2 rounded-lg border border-slate-200 px-3 py-2 text-xs dark:border-slate-800">
          <summary className="cursor-pointer select-none">
            {previousAsOf} 대비 새로 {added.size}건 · 빠짐 {dropped.length}건
          </summary>
          {dropped.length > 0 ? (
            <ul className="mt-1 list-disc pl-5 text-slate-600 dark:text-slate-300">
              {dropped.map((d) => (
                <li key={signalKey(d)}>
                  <Link href={`/stocks/${d.stock_id}`} className="hover:underline">{d.name}</Link>
                  <span className="ml-1 text-slate-400">{d.ticker} · {HORIZON_LABEL[d.horizon]?.split(" ")[0] ?? d.horizon}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-slate-500">빠진 종목이 없습니다.</p>
          )}
          <p className="mt-1 text-slate-400">조건을 만족하지 못해 빠진 것이지 오류가 아닙니다. 왜 빠졌는지는 종목 상세의 마지막 신호 근거표에서 봅니다.</p>
        </details>
      ) : null}

      {notes.map((note) => (
        <p
          key={note}
          className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:bg-amber-950 dark:text-amber-200"
        >
          {note}
        </p>
      ))}

      {groups.length === 0 ? (
        <div className="py-8 text-sm text-slate-500">
          <p>표시할 추천이 없습니다.</p>
          {/*
            **갈 곳을 준다** (2026-09-21, docs/infra.md 25.89).
            이 화면은 "규칙이 고른 결과" 를 보는 곳이고, 짝이 되는 "내가 조건을 정해 고르는 곳" 은
            스크리너다. 스크리너의 뼈대 질의는 `stocks JOIN universe_members` 뿐이고
            재무·성과지표·감성은 전부 LEFT JOIN 이라, **그것들이 0행이어도 종목 목록은 나온다** —
            시가총액·거래대금으로 거르고 줄 세울 수 있다. 추천이 비는 동안 쓸 수 있는 유일한 길이다.

            위의 `notes` 는 평문으로 찍히므로(`{note}`) 링크를 글자로 넣을 수 없다. 여기서 단다.
          */}
          <p className="mt-2">
            <Link href="/screener" className="text-sky-700 underline dark:text-sky-400">
              종목 찾기
            </Link>
            <span> 에서는 시가총액·거래대금으로 직접 골라 볼 수 있습니다 (재무가 필요한 PER·PBR·ROE 칸은 아직 빕니다).</span>
          </p>
        </div>
      ) : (
        <>
          <HorizonTabs
            active={active}
            counts={counts}
            onChange={(next) => {
              setHorizon(next);
              saveTab(next);
            }}
          />
          {visible.map((group) => (
            <section key={group.horizon} className="mb-6">
              {active === "all" ? (
                <h2 className="mb-2 text-sm font-semibold">
                  {group.label}
                  <span className="ml-2 font-normal text-slate-500">
                    {group.rows.length}건
                  </span>
                </h2>
              ) : null}
              <div className="flex flex-col gap-2">
                {group.rows.map((row) => (
                  <Card
                    key={`${row.stock_id}-${row.horizon}`}
                    row={row}
                    isNew={added.has(signalKey(row))}
                    watched={watchIds.has(row.stock_id)}
                    onToggleWatch={() => void toggleWatch(row.stock_id)}
                  />
                ))}
              </div>
            </section>
          ))}
        </>
      )}
    </div>
  );
}

/**
 * 상단 요약 한 줄.
 *
 * 매일 여는 화면이라면 "이게 언제 것인가" 가 첫 질문이다. 신호 계산 기준일과
 * 마지막 배치 실행 시각을 둘 다 보여준다. 둘이 다를 수 있다 — 기준일은 데이터의
 * 날짜이고 실행 시각은 배치가 실제로 돈 시각이다(예약 실행은 지연된다).
 * 기준일이 오래됐으면 눈에 띄게 한다. 배치가 안 돌았는데 화면이 멀쩡해 보이면 안 된다.
 */
function Summary({
  count,
  counts,
  asOf,
  lastRun,
}: {
  count: number;
  counts: Record<string, number>;
  asOf: string | null;
  lastRun: LastRun | null;
}) {
  const stale = isStale(asOf);
  const age = daysSince(asOf);
  const ran = formatKst(lastRun?.finished_at ?? null);

  return (
    <div className="mb-3 rounded-lg border border-slate-200 px-3 py-2 text-xs dark:border-slate-800">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium">신호 {count}건</span>
        <span className="text-slate-400">
          단기 {counts.short ?? 0} · 중기 {counts.mid ?? 0} · 장기 {counts.long ?? 0}
        </span>
      </div>
      <div
        className={`mt-1 flex flex-wrap gap-x-2 ${
          stale ? "text-amber-700 dark:text-amber-300" : "text-slate-500 dark:text-slate-400"
        }`}
      >
        <span>
          기준일 {asOf ?? "-"}
          {age !== null && age > 0 ? ` (${age}일 전)` : ""}
        </span>
        {ran ? <span>· 마지막 배치 {ran} KST</span> : null}
        {stale ? (
          <span className="font-medium">
            · {STALE_AFTER_DAYS}일 넘게 새로 계산되지 않았습니다. 배치를 확인하세요
          </span>
        ) : null}
      </div>
    </div>
  );
}

/**
 * 카드. 기본은 접혀 있다.
 *
 * 접힌 상태는 "무엇을 + 왜" 만 보여준다. 종목·점수·근거 한 문장. 그것으로
 * 관심 여부를 가를 수 있고, 관심이 가면 탭해서 구간·분할·금액·근거표를 본다.
 * 17건이 한 화면에 들어오는 것이 목표다.
 */
function Card({
  row, isNew = false, watched = false, onToggleWatch,
}: { row: RecommendRow; isNew?: boolean; watched?: boolean; onToggleWatch?: () => void }) {
  const [open, setOpen] = useState(false);
  const factors = parseFactors(row.factor_scores);
  const tranches = parseTranches(row.tranche_plan);
  const criteria = parseCriteria(row.rationale_data);
  const cut = reductionNote(row.size_reduction, row.rationale_data);

  return (
    <article className="rounded-xl border border-slate-200 dark:border-slate-800">
      {/* 접힌 머리. 버튼이라 폰에서 어디를 눌러도 펼쳐진다 */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full px-3 py-2.5 text-left"
      >
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          {isNew ? <span className="rounded bg-sky-100 px-1 py-0.5 text-[10px] font-semibold text-sky-800 dark:bg-sky-900 dark:text-sky-200">NEW</span> : null}
          <span className="font-semibold">{row.name}</span>
          <span className="text-xs text-slate-500">{row.ticker}</span>
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            {row.signal_type}
          </span>
          <span className="ml-auto text-sm">
            종합 <b>{score(row.total_score)}</b>
            {row.rank_in_market ? (
              <span className="ml-1 text-xs text-slate-500">시장 {row.rank_in_market}위</span>
            ) : null}
            {/* 점수의 기준일과 출처 — 근거표에 종합 점수 행이 없어 어느 날 점수인지 알 수 없었다 (25.911) */}
            {row.score_date ? (
              <span className="ml-1 text-xs text-slate-400">· {String(row.score_date).slice(0, 10)} 점수(scores)</span>
            ) : null}
            <span className="ml-2 text-xs text-slate-400">{open ? "▲" : "▼"}</span>
          </span>
        </div>
        {row.rationale_text ? (
          <p
            className={`mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-300 ${
              open ? "" : "line-clamp-2"
            }`}
          >
            {row.rationale_text}
          </p>
        ) : null}
      </button>

      {open ? (
        <div className="border-t border-slate-100 px-3 pb-3 pt-2 dark:border-slate-800">
          <div className="mb-1 flex flex-wrap items-center justify-end gap-3 text-xs">
            {onToggleWatch ? (
              <button
                type="button"
                onClick={onToggleWatch}
                title="관심 종목은 장중 5분마다 급등락·거래량·목표 매수가를 감시합니다"
                className={`min-h-10 rounded-lg border px-3 sm:min-h-0 sm:rounded sm:px-2 sm:py-0.5 ${watched
                  ? "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-200"
                  : "border-slate-300 text-slate-600 dark:border-slate-700 dark:text-slate-300"}`}
              >
                {watched ? "관심 해제" : "관심 등록"}
              </button>
            ) : null}
            <Link href={`/stocks/${row.stock_id}`} className="inline-flex min-h-10 items-center text-blue-700 underline sm:min-h-0 dark:text-blue-300">종목 상세</Link>
            {/* 그 자리에서 매매 입력 (25.894). 관심·종목 상세와 같은 줄, 폼은 아랫줄 전체 (25.895) */}
            <QuickTrade
              stock={{
                id: row.stock_id,
                ticker: row.ticker,
                name: row.name,
                country: row.currency === "KRW" ? "KR" : "US",
                currency: row.currency,
                market: row.market,
              }}
              horizon={row.horizon === "short" || row.horizon === "mid" ? row.horizon : "long"}
            />
          </div>
          {/* 팩터. 센티먼트는 별도 축이라 여기 섞지 않는다 */}
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-600 dark:text-slate-300">
            {FACTORS.map(([key, label]) => (
              <span key={key}>
                {label} <b>{score(factors[key])}</b>
              </span>
            ))}
          </div>
          {/* 센티먼트는 섞지 않되 **빼지도 않는다** — 따로 한 줄 (docs/infra.md 25.379) */}
          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{sentimentLine(row)}</p>
          {/* 과거 성과 요약 — 1부 약속(docs/design.md 3.9), 텔레그램 1부와 같은 값 (docs/infra.md 25.803) */}
          {performanceLine(row.rationale_data) ? (
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{performanceLine(row.rationale_data)}</p>
          ) : null}

          {/* 얼마에 */}
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
            <div>
              <dt className="text-slate-500">현재가</dt>
              {/* 가장 새 종가다 — 신호 기준일과 다를 수 있어 그 날짜를 붙인다 (docs/infra.md 25.269) */}
              <dd>
                {money(row.close, row.currency)}
                {row.price_date ? <span className="ml-1 text-slate-400">({row.price_date})</span> : null}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">매수 구간</dt>
              <dd>
                {money(row.buy_zone_low, row.currency)} ~ {money(row.buy_zone_high, row.currency)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">목표가</dt>
              <dd>{money(row.target_price, row.currency)}</dd>
            </div>
            <div>
              <dt className="text-slate-500">손절선</dt>
              <dd>{money(row.stop_price, row.currency)}</dd>
            </div>
          </dl>

          {/* 3회 분할 */}
          {tranches.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-2 text-xs">
              {tranches.map((t) => (
                <span
                  key={t.step}
                  className="rounded border border-slate-200 px-2 py-1 dark:border-slate-700"
                >
                  {t.step}차 {Math.round(t.ratio * 100)}% · {money(t.price, row.currency)}
                  {t.amount !== null ? ` · ${money(t.amount, row.currency)}` : ""}
                </span>
              ))}
            </div>
          ) : null}

          {/* 얼마나. 금액이 작으면 그 이유를 함께 밝힌다 */}
          <div className="mt-2 text-xs text-slate-600 dark:text-slate-300">
            권장 비중{" "}
            <b>
              {row.suggested_weight_pct === null ? "-" : `${row.suggested_weight_pct.toFixed(1)}%`}
            </b>
            {row.suggested_amount !== null ? (
              <>
                {" "}
                · 권장 금액 <b>{money(row.suggested_amount, row.currency)}</b>
              </>
            ) : (
              <span className="ml-1 text-slate-500">
                · 권장 금액 없음 (까닭은 근거표의 &lsquo;권장 금액&rsquo; 행)
              </span>
            )}
            {/* **1부(개별 종목) 기준이다** (docs/infra.md 25.272). 리포트 2부는 보유·상관·섹터 상한을 반영해 다시 줄이므로
                같은 종목이 텔레그램 2부에서는 더 작은 비중·금액으로 나올 수 있다 — 두 숫자가 다르다고 오류가 아니다 */}
            {/* 리포트는 시장별 상위 REPORT_TOP_STOCKS 개만 싣는다 — 그 밖의 카드는 2부에 없다 (25.586, 감사) */}
            <span className="ml-1 text-slate-400">(보유·상관·섹터 상한 반영 전 — 리포트는 시장별 상위 {REPORT_TOP_STOCKS}종목만 싣고 2부에서 반영합니다)</span>
          </div>

          {cut ? <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">{cut}</p> : null}
          {row.sector_cap_note ? (
            <p className="mt-1 text-xs text-slate-500">{row.sector_cap_note}</p>
          ) : null}

          {/*
            확인. 문장만으로는 확인이 안 된다. 값·문턱·출처·기준일을 표로 편다.

            **0줄이어도 그린다** (2026-09-23, docs/infra.md 25.174). 예전에는
            `criteria.length > 0` 으로 감쌌다. 그러면 근거가 한 줄도 없을 때 표도 경고도
            없이 **추천 카드만 남는다** — 확인할 수 없는 추천이 확인할 수 있는 것과
            똑같이 보인다. `CriteriaTable` 이 0줄일 때 빨간 경고를 그리는 이유가 그것인데
            (25.71) 이 조건이 그 경고를 **부를 수 없게** 막고 있었다
          */}
          <CriteriaTable
            rows={criteria}
            caption={`신호 계산일 ${row.as_of_date}`}
            docPath="docs/signals.md"
            staleAfterDays={STALE_AFTER_DAYS}
          />
        </div>
      ) : null}
    </article>
  );
}

const TAB_STORAGE_KEY = "recommend.horizon";

/**
 * 마지막에 본 기간 탭. 브라우저 저장소가 막혀 있어도(사생활 모드 등)
 * 기본값으로 조용히 돌아간다. 기억은 편의일 뿐 화면의 전제가 아니다.
 */
function readSavedTab(): HorizonTab {
  try {
    const saved = window.localStorage.getItem(TAB_STORAGE_KEY);
    if (saved === "all" || (HORIZON_ORDER as readonly string[]).includes(saved ?? "")) {
      return saved as HorizonTab;
    }
  } catch {
    // 저장소를 못 쓰는 환경이다. 기본값으로 간다
  }
  return "short";
}

function saveTab(tab: HorizonTab) {
  try {
    window.localStorage.setItem(TAB_STORAGE_KEY, tab);
  } catch {
    // 기억하지 못해도 된다
  }
}

/**
 * 기간 탭.
 *
 * 단기·중기·장기는 판단 근거가 달라 섞지 않는다는 규칙은 그대로다.
 * 다만 세 묶음을 세로로 다 펼치면 폰에서 너무 길어, 한 번에 한 묶음만 본다.
 * 건수를 탭에 붙여 다른 기간에 무엇이 있는지 안 보고도 알게 한다.
 */
function HorizonTabs({
  active,
  counts,
  onChange,
}: {
  active: HorizonTab;
  counts: Record<string, number>;
  onChange: (next: HorizonTab) => void;
}) {
  const tabs: Array<{ key: HorizonTab; label: string }> = [
    ...HORIZON_ORDER.map((h) => ({ key: h, label: HORIZON_LABEL[h].split(" ")[0] })),
    { key: "all", label: "전체" },
  ];

  return (
    <div role="tablist" className="mb-3 flex gap-1 rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
      {tabs.map((tab) => {
        const selected = tab.key === active;
        const count = counts[tab.key] ?? 0;
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={selected}
            disabled={count === 0 && tab.key !== "all"}
            onClick={() => onChange(tab.key)}
            className={`min-h-11 flex-1 rounded-md px-2 text-sm transition sm:min-h-0 sm:py-1.5 ${
              selected
                ? "bg-white font-medium shadow-sm dark:bg-slate-900"
                : "text-slate-600 hover:bg-white/60 disabled:opacity-40 dark:text-slate-300 dark:hover:bg-slate-900/60"
            }`}
          >
            {tab.label}
            <span className="ml-1 text-xs text-slate-400">{count}</span>
          </button>
        );
      })}
    </div>
  );
}
