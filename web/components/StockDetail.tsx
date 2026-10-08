"use client";

import AnalyzeNow from "@/components/AnalyzeNow";
import ForecastTable from "@/components/ForecastTable";
import ForecastTrack from "@/components/ForecastTrack";
import VerdictBlock from "@/components/VerdictBlock";
import type { AnalysisRequest, ReferenceScoreData, Verdict } from "@/lib/analysis";
import Link from "next/link";
import { cachedFetch } from "@/lib/clientCache";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import CriteriaTable from "@/components/CriteriaTable";
import FlagCriteria from "@/components/FlagCriteria";
import PriceChart, { type Bar } from "@/components/PriceChart";
import { HORIZON_LABEL, STALE_AFTER_DAYS, parseCriteria } from "@/lib/recommend";
import {
  FACTOR_LABELS,
  bandPositions,
  formatAmount,
  formatNum,
  formatPct,
  formatPercentNumber,
  eventOrigin,
  formatPrice,
  overlayNote,
  priceAdjustLabel,
  dividendSourceLine,
  overlaysFor,
  recoveryText,
  radarPoints,
  sentimentEmptyReason,
  riskSourceNote,
  type Row,
  type SectionResult,
 metricLabel,
  substitutedNote,
  priceRiskNote,
  QUARTER_LABEL,
  REPORT_LABEL,
  staleSentimentNote,
  SCORE_STALE_DAYS,
  scoreStaleNote,
  signalScoreDateNote,
  sentimentNullReason,
  ETF_SECTION_NOTE,
  marketCapNote,
} from "@/lib/stockDetail";
import { unmatchedNameNote } from "@/lib/newsKr";
import { money, pnlClass, qtyText, signedInt, signedWon } from "@/lib/portfolio";
import { localDate, userDateOf, userTimeOf } from "@/lib/market";
import { addWatch, loadWatchIds, removeWatch } from "@/lib/watch";
import QuickTrade from "@/components/QuickTrade";
import { type BrokerStat, brokerLine } from "@/lib/brokers";

/**
 * 종목 상세 (docs/stock_detail.md, Step 10).
 *
 * 섹션마다 따로 불러 따로 그린다. 한 섹션이 비거나 실패해도 나머지는 보인다.
 * 모든 섹션 머리에 기준 시각을, 비었으면 "데이터 없음" 과 사유를 적는다.
 */

type Loaded<T> = { state: "loading" } | { state: "error"; message: string } | { state: "ok"; data: T };

interface Overview {
  stock: Row;
  score: (Row & { factor_scores: Record<string, number | null> }) | null;
  factors: Array<Row & { raw: Record<string, unknown>; missing: string[] }>;
  position: Row | null;
  /** 보유가 매매 기록보다 늦은가(재계산 전). null 은 모름 (25.806) */
  position_stale?: boolean | null;
  /** 재계산이 멈췄으면 그 말 — 포트폴리오 화면과 같은 문구 (25.809) */
  position_stuck?: string | null;
  flags: Row[];
  watch: Row | null;
  /** 못 읽은 머리 항목 이름 (25.590) */
  unread?: string[];
}

function useJson<T>(url: string, enabled = true): Loaded<T> {
  const [value, setValue] = useState<Loaded<T>>({ state: "loading" });
  useEffect(() => {
    // 아직 화면 가까이 오지 않은 카드는 읽지 않는다 (docs/infra.md 25.1032)
    if (!enabled) return;
    let alive = true;
    setValue({ state: "loading" });
    cachedFetch(url)
      .then(async (r) => {
        const body = await r.json();
        if (!alive) return;
        if (!r.ok) setValue({ state: "error", message: body?.errors?.[0] ?? `HTTP ${r.status}` });
        else setValue({ state: "ok", data: body as T });
      })
      .catch((e) => alive && setValue({ state: "error", message: String(e) }));
    return () => {
      alive = false;
    };
  }, [url, enabled]);
  return value;
}

/**
 * 아래쪽 카드는 **화면 가까이 왔을 때** 읽는다 (docs/infra.md 25.1032, 2026-10-08 사용자 "웹 화면 DB 사용량도 줄여줘").
 * 종목 화면을 열 때마다 열세 구역을 한꺼번에 읽었다 — 위쪽 넷(개요·분석·가격·신호) 말고는 스크롤해야 보이는데,
 * 대개 거기까지 내려가지 않고 다른 종목으로 간다. `data-lazy` 가 달린 카드가 화면 아래 `LAZY_MARGIN` 안에 들어오면 읽는다.
 * 바로가기(재무·뉴스…)를 누르면 그 자리로 스크롤되며 읽힌다. 관찰기가 없는 브라우저면 한꺼번에 읽는다(예전과 같다).
 */
const LAZY_MARGIN = "800px 0px";

function useSeen(ready: boolean, key: string): Record<string, boolean> {
  const [seen, setSeen] = useState<Record<string, boolean>>({});
  // 다른 종목으로 가면 처음부터 — 앞 종목에서 본 카드를 새 종목에서 바로 읽지 않는다
  useEffect(() => setSeen({}), [key]);
  useEffect(() => {
    if (!ready) return;
    const els = Array.from(document.querySelectorAll<HTMLElement>("[data-lazy]"));
    if (typeof IntersectionObserver === "undefined") {
      setSeen(Object.fromEntries(els.map((e) => [e.dataset.lazy ?? "", true])));
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        const 새것 = entries.filter((e) => e.isIntersecting).map((e) => e.target as HTMLElement);
        if (!새것.length) return;
        새것.forEach((e) => io.unobserve(e));
        setSeen((s) => ({ ...s, ...Object.fromEntries(새것.map((e) => [e.dataset.lazy ?? "", true])) }));
      },
      { rootMargin: LAZY_MARGIN },
    );
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, [ready, key]);
  return seen;
}

/** 폰 구역 바로가기가 가리키는 곳. 위쪽 제목줄(3rem)과 바로가기 줄 아래로 내려앉게 띄운다 */
const ANCHOR = "scroll-mt-28 sm:scroll-mt-4";

function Card({
  id,
  title,
  loaded,
  children,
  right,
  emptyNote,
  lazy,
}: {
  /** 화면 가까이 왔을 때 읽는 카드의 이름 — `useSeen` 이 본다 (25.1032) */
  lazy?: string;
  id?: string;
  title: string;
  loaded: Loaded<SectionResult>;
  children: (data: SectionResult) => ReactNode;
  right?: ReactNode;
  /** 비었을 때 서버 까닭 대신 보일 글 — ETF 의 보통주 전용 구역 (25.940) */
  emptyNote?: string;
}) {
  // 데이터가 없는 구역은 한 줄로 줄인다. 빈 카드 열 개가 세로로 쌓여 화면이 길었다 (2026-09-18 "화면이 너무 길다")
  if (loaded.state === "ok" && loaded.data.empty) {
    return (
      <section id={id} data-lazy={lazy} className={`mb-2 rounded-xl border border-slate-200 px-3 py-2 sm:mb-4 dark:border-slate-800 ${ANCHOR}`}>
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <h2 className="text-sm font-semibold">{title}</h2>
          <span className="text-xs text-slate-400">{emptyNote ?? `데이터 없음${loaded.data.reason ? ` — ${loaded.data.reason}` : ""}`}</span>
          {right ? <div className="ml-auto flex items-center gap-2 text-xs text-slate-500">{right}</div> : null}
        </div>
      </section>
    );
  }
  return (
    <section id={id} data-lazy={lazy} className={`mb-4 rounded-xl border border-slate-200 p-3 dark:border-slate-800 ${ANCHOR}`}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
          {right}
          {loaded.state === "ok" && loaded.data.as_of && <span>기준 {shortTime(loaded.data.as_of)}</span>}
        </div>
      </div>
      {loaded.state === "loading" && <p className="text-xs text-slate-400">불러오는 중…</p>}
      {loaded.state === "error" && <p className="text-xs text-red-600">불러오지 못했습니다: {loaded.message}</p>}
      {loaded.state === "ok" && !loaded.data.empty && children(loaded.data)}
    </section>
  );
}

/** 폰 구역 바로가기. 카드 id 와 같아야 한다 */
const JUMPS: Array<[string, string]> = [
  ["verdict", "분석"],
  ["price", "가격"],
  ["signals", "신호"],
  ["metrics", "성과"],
  ["financials", "재무"],
  ["news", "뉴스"],
  ["events", "일정"],
  ["opinions", "의견"],
];

/** "2026-09-17" 은 그대로, ISO 시각은 KST 로 */
function shortTime(value: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("ko-KR", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

const FLAG_STYLE: Record<string, string> = {
  red: "border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200",
  yellow: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200",
  green: "border-green-300 bg-green-50 text-green-800 dark:border-green-900 dark:bg-green-950 dark:text-green-200",
};

export default function StockDetail({
  id,
  trade,
  horizon: horizonParam,
}: {
  id: number;
  /** 주소의 ?trade=buy|sell — 아침 리포트 딥링크 (25.944). 있으면 매매 폼을 펼친 채 연다 */
  trade?: "buy" | "sell";
  horizon?: "short" | "mid" | "long";
}) {
  const base = `/api/stocks/${id}`;
  const overview = useJson<Overview>(base);
  // 아래쪽 카드는 화면 가까이 왔을 때 읽는다 (25.1032)
  const seen = useSeen(overview.state === "ok", base);
  const [range, setRange] = useState<"3M" | "1Y" | "3Y" | "5Y">("1Y");
  const prices = useJson<SectionResult>(`${base}/prices?range=${range}`);
  const signals = useJson<SectionResult>(`${base}/signals`);
  const valuation = useJson<SectionResult>(`${base}/valuation`, !!seen.valuation);
  const metrics = useJson<SectionResult>(`${base}/metrics`, !!seen.metrics);
  const financials = useJson<SectionResult>(`${base}/financials`, !!seen.financials);
  const news = useJson<SectionResult>(`${base}/news`, !!seen.news);
  const events = useJson<SectionResult>(`${base}/events`, !!seen.events);
  const dividends = useJson<SectionResult>(`${base}/dividends`, !!seen.dividends);
  const quarterly = useJson<SectionResult>(`${base}/quarterly`, !!seen.quarterly);
  // 증권사 의견과 그 증권사의 성적 (docs/brokers.md, 25.995)
  const opinions = useJson<SectionResult>(`${base}/opinions`, !!seen.opinions);
  const verdict = useJson<SectionResult>(`${base}/verdict`);
  const [horizon, setHorizon] = useState<string | null>(horizonParam ?? null);
  // 관심 등록·해제 뒤 화면 값. undefined 면 서버가 준 것을 쓴다 (훅은 early return 위에)
  const [watchOverride, setWatchOverride] = useState<{ id: number } | null | undefined>(undefined);

  if (overview.state === "loading") return <p className="text-sm text-slate-400">불러오는 중…</p>;
  if (overview.state === "error") return <p className="text-sm text-red-600">{overview.message}</p>;
  const { stock, score, factors, position, position_stale: positionStale = null, position_stuck: positionStuck = null, flags, watch: watchFromServer, unread = [] } = overview.data;
  const etf = stock.asset_type === "etf";
  // 유니버스 밖 종목의 참고 점수 — "지금 분석"·관심 종목 매일 분석이 만든다 (docs/analysis.md 8장, 25.1019)
  const 참고 = verdict.state === "ok" && (verdict.data.verdict as Verdict | null)?.verdict === "reference"
    ? (verdict.data.verdict as Verdict).detail : null;
  const 참고점수 = 참고?.reference_score ?? null;
  const 참고사유 = 참고?.excluded_reason ?? null;
  // 재계산 전 보유로 평균 단가 선을 그리지 않는다 — 방금 판 종목의 옛 단가가 지금 값처럼 보였다 (25.806)
  const 보유상태: "ok" | "unread" | "stale" = unread.includes("보유") ? "unread" : positionStale ? "stale" : "ok";
  const watch = watchOverride === undefined ? watchFromServer : watchOverride;
  const toggleWatch = async () => {
    const res = watch ? await removeWatch(Number(watch.id)) : await addWatch(id);
    if (!res.ok) {
      window.alert(`관심 종목 ${watch ? "해제" : "등록"} 실패: ${res.error}`);
      return;
    }
    const ids = await loadWatchIds();
    if (ids === null) {
      // 바꾸기는 됐는데 목록을 다시 못 읽었다 (25.815). 해제는 "등록 안 됨" 으로 바꾼다. 등록은 새 관심 번호를 몰라 단추를 바꾸지 못한다 —
      // 알림 글이 새로고침을 권한다(다시 눌러도 upsert 라 해는 없다, 25.817 교차검증)
      window.alert(`관심 종목 ${watch ? "해제" : "등록"}은 됐지만 목록을 다시 읽지 못했습니다. 새로고침하면 바로 보입니다`);
      if (watch) setWatchOverride(null);
      return;
    }
    const newId = ids.get(id);
    setWatchOverride(newId ? { id: newId } : null);
  };
  const currency = String(stock.currency);
  const signalRows = signals.state === "ok" ? ((signals.data.rows as Row[]) ?? []) : [];
  const chosen = signalRows.find((r) => r.horizon === horizon) ?? signalRows[0] ?? null;

  return (
    <div>
      {/* 머리 */}
      {unread.length > 0 ? (
        <p className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          {unread.join("·")} 을(를) 읽지 못했습니다 — 그 칸은 비어 보이지만 없는 것이 아닙니다
        </p>
      ) : null}
      <header className="mb-4">
        <div className="flex flex-wrap items-baseline gap-2">
          <h1 className="text-lg font-semibold">{String(stock.name)}</h1>
          <span className="text-sm text-slate-500">
            {String(stock.ticker)} · {String(stock.market)}
            {stock.sector ? ` · ${String(stock.sector)}` : " · 업종 없음"}
          </span>
          {stock.status !== "active" && <span className="rounded bg-slate-200 px-1.5 text-xs dark:bg-slate-700">{String(stock.status)}</span>}
          <button
            type="button"
            onClick={() => void toggleWatch()}
            title="관심 종목은 장중 5분마다 급등락·거래량·목표 매수가를 감시합니다"
            className={`min-h-11 rounded-lg border px-3 text-sm sm:min-h-0 sm:rounded sm:px-1.5 sm:text-xs ${watch
              ? "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-100"
              : "border-slate-300 text-slate-600 dark:border-slate-700 dark:text-slate-300"}`}
          >
            {watch ? "관심 종목 · 해제" : "관심 등록"}
          </button>
          {/* 그 자리에서 매매 입력 (25.894). 관심 단추 옆, 폼은 아랫줄 전체 (25.895) */}
          <QuickTrade
            stock={{
              id,
              ticker: String(stock.ticker),
              name: String(stock.name),
              country: String(stock.country ?? ""),
              currency,
              market: String(stock.market),
              // ETF 면 매도 세금 칸이 "증권거래세 없음 · 비우면 0" 으로 안내한다 (25.904)
              asset_type: String(stock.asset_type ?? "stock"),
            }}
            horizon={horizon === "short" || horizon === "mid" || horizon === "long" ? horizon : "long"}
            initialOpen={trade !== undefined}
            initialSide={trade}
          />
        </div>
        {flags.length > 0 && (
          <ul className="mt-2 space-y-1">
            {flags.map((f) => (
              // 확인한 플래그는 흐리게 — 포트폴리오 화면과 같다 (25.811). 여전히 보이게는 둔다(자동으로 지우지 않는다)
              <li key={String(f.id)} className={`rounded-lg border px-3 py-1.5 text-xs ${FLAG_STYLE[String(f.level)] ?? ""} ${f.dismissed_at ? "opacity-60" : ""}`}>
                <b>매도 플래그 · {String(f.reason_code)}</b> {String(f.rationale_text)}
                <span className="ml-1 opacity-70">({String(f.first_seen_date)}부터{f.dismissed_at ? " · 확인함" : ""})</span>
                <FlagCriteria raw={f.rationale_data} asOf={f.as_of_date} />
              </li>
            ))}
          </ul>
        )}
      </header>

      {/* 폰: 구역 바로가기. 카드 열 개를 끝까지 내려 찾던 것을 한 번에 간다 (2026-09-18 "화면이 너무 길다") */}
      <nav
        aria-label="구역 바로가기"
        className="sticky top-[calc(3rem+env(safe-area-inset-top))] z-10 -mx-4 mb-3 flex gap-1 overflow-x-auto border-b border-slate-200 bg-slate-50 px-4 py-1.5 [scrollbar-width:none] sm:hidden dark:border-slate-800 dark:bg-slate-950 [&::-webkit-scrollbar]:hidden"
      >
        {/* 여섯이 390px 폭에 한 줄로 들어가게 칩 여백을 줄였다(2026-09-18 운영 확인에서 "일정" 이 잘렸다) */}
        {JUMPS.map(([anchor, label]) => (
          <a
            key={anchor}
            href={`#${anchor}`}
            className="flex h-10 flex-1 shrink-0 items-center justify-center rounded-full border border-slate-300 bg-white px-2.5 text-sm active:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:active:bg-slate-800"
          >
            {label}
          </a>
        ))}
      </nav>

      {/* 예상 주가 표 — 화면 맨 위 (docs/analysis.md 10장, 25.1025 사용자 요청) */}
      {(() => {
        const o = verdict.state === "ok" ? (verdict.data.verdict as Verdict | null)?.detail?.outlook : null;
        return o?.forecast ? <ForecastTable f={o.forecast} close={o.close} closeDate={o.close_date} currency={currency} /> : null;
      })()}
      {/* 예측 성적표 — 예상 주가 바로 아래 (docs/analysis.md 11장, 25.1037) */}
      {(() => {
        const t = verdict.state === "ok" ? (verdict.data.verdict as Verdict | null)?.detail?.track : null;
        return t ? <ForecastTrack t={t} /> : null;
      })()}

      {/* 점수: 팩터 레이더와 센티먼트는 분리해 보인다 (CLAUDE.md 스코어링) */}
      {!score ? (
        <section className={`mb-2 rounded-xl border border-slate-200 px-3 py-2 sm:mb-4 dark:border-slate-800 ${ANCHOR}`}>
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <h2 className="text-sm font-semibold">점수</h2>
            <span className="text-xs text-slate-400">
              {/* 못 읽은 것을 "계산되지 않았다" 로 단정하지 않는다 (docs/infra.md 25.592, 교차검증) */}
              {/* ETF 는 점수를 내지 않는다 — "아직" 이라 하면 기다리면 생길 것처럼 읽힌다 (25.905) */}
              {unread.includes("종합 점수") ? "점수를 읽지 못했습니다 — 계산되지 않은 것이 아닐 수 있습니다"
                : stock.asset_type === "etf" ? "ETF 는 종목 점수를 내지 않습니다 — 장기 적립 관점의 판정은 ETF 탭에 있습니다"
                : 참고점수 ? `참고 점수 — 유니버스 밖(${참고사유 ?? "제외"})이라 추천 점수표에는 넣지 않습니다`
                : "데이터 없음 — 점수는 유니버스 편입 종목만 매일 냅니다. 유니버스 밖 종목은 아래 '지금 분석' 으로 참고 점수를 냅니다"}
            </span>
          </div>
          {참고점수 && <ReferenceScore s={참고점수} />}
        </section>
      ) : (
        <section className="mb-4 rounded-xl border border-slate-200 p-3 dark:border-slate-800">
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold">점수</h2>
            <span className="text-xs text-slate-500">기준 {String(score.as_of_date)}</span>
          </div>
          {(() => {
            const 묵음 = scoreStaleNote(score.as_of_date as string | null, localDate(String(stock.country ?? "") === "US" ? "US" : "KR", new Date()));
            return 묵음 ? <p className="mb-2 text-xs text-amber-700 dark:text-amber-300">{묵음}</p> : null;
          })()}
          <ScoreBlock score={score} factors={factors} currency={currency} />
        </section>
      )}

      {positionStale && (
        <p className="mb-2 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          {/* 멈췄으면 그 까닭, 아니면 "계산 중". 지문은 전체 매매 기준이라 이 종목의 매매인지는 모른다 — 보유가 없을 때는 중립으로 (25.809) */}
          {positionStuck ?? "매매 기록이 바뀌어 보유를 다시 계산하는 중입니다(보통 1~2분)."}{" "}
          {보유상태 === "unread" ? "" : position ? "아래 보유는 바뀌기 전 값일 수 있습니다(다른 종목의 매매일 수도 있습니다)." : "이 종목을 새로 샀다면 계산이 끝난 뒤 보유가 보입니다."}
        </p>
      )}
      {position && <PositionBlock position={position} currency={currency} stale={positionStale === true} />}

      <Card
        id="verdict"
        title="종목 분석 의견"
        loaded={verdict}
        right={
          // 유니버스 밖 종목만 — 의견이 없거나 참고 분석·판단 보류일 때 (docs/analysis.md 8장, 25.1018)
          !etf && verdict.state === "ok" && (!verdict.data.verdict
            || ["reference", "undecided"].includes(String((verdict.data.verdict as Verdict).verdict))) ? (
            <AnalyzeNow stockId={Number(stock.id)} request={(verdict.data.request as AnalysisRequest | null | undefined) ?? null} />
          ) : null
        }
      >
        {(data) => <VerdictBlock v={data.verdict as Verdict} />}
      </Card>

      <Card
        id="price"
        title="가격"
        loaded={prices}
        right={
          <span className="flex gap-1">
            {(["3M", "1Y", "3Y", "5Y"] as const).map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setRange(r)}
                className={`h-10 min-w-10 rounded px-1.5 sm:h-auto sm:min-w-0 sm:py-0.5 ${range === r ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900" : "hover:bg-slate-100 dark:hover:bg-slate-800"}`}
              >
                {r}
              </button>
            ))}
          </span>
        }
      >
        {(data) => (
          <PriceBlock
            data={data} signal={chosen} signalRows={signalRows} position={보유상태 === "ok" ? position : null} positionState={보유상태} onHorizon={setHorizon} currency={currency}
            signalsState={signals.state} signalAsOf={signals.state === "ok" ? ((signals.data.as_of as string | null) ?? null) : null}
            country={String(stock.country ?? "")}
          />
        )}
      </Card>

      <Card id="signals" title="현재 신호와 매수 계획" loaded={signals} emptyNote={etf ? ETF_SECTION_NOTE.signals : undefined}>
        {(data) => (
          <SignalsBlock
            rows={data.rows as Row[]} checks={(data.checks as Judged[]) ?? []} reason={data.reason as string | null} currency={currency}
            runNote={(data.run_note as string | null | undefined) ?? null}
            reference={data.reference === true}
            dateNote={data.reference === true ? null : signalScoreDateNote((data.as_of as string | null) ?? null, (score?.as_of_date as string | undefined) ?? null)}
          />
        )}
      </Card>

      <Card lazy="valuation" title="밸류에이션 밴드 (PBR 3년)" loaded={valuation} emptyNote={etf ? ETF_SECTION_NOTE.valuation : undefined}>
        {(data) => <ValuationBlock band={data.band as Row} factors={factors} currency={currency} />}
      </Card>

      <Card lazy="metrics" id="metrics" title="과거 성과" loaded={metrics}>
        {(data) => <MetricsBlock rows={data.rows as Row[]} />}
      </Card>

      {/* 재무 셋을 붙여 둔다(전에는 뉴스가 사이에 끼어 있었다). 바로가기 "재무" 가 여기로 온다 */}
      <Card lazy="financials" id="financials" title="5년 재무 (사업보고서)" loaded={financials} emptyNote={etf ? ETF_SECTION_NOTE.financials : undefined}>
        {(data) => <FinancialsBlock rows={data.rows as Row[]} />}
      </Card>

      <Card lazy="quarterly" title="분기·반기 재무" loaded={quarterly} emptyNote={etf ? ETF_SECTION_NOTE.quarterly : undefined}>
        {(data) => <QuarterlyBlock rows={data.rows as Row[]} />}
      </Card>

      <Card lazy="dividends" title="배당 이력" loaded={dividends} emptyNote={etf ? ETF_SECTION_NOTE.dividends : undefined}>
        {(data) => <DividendsBlock rows={data.rows as Row[]} currency={currency} />}
      </Card>

      <Card lazy="news" id="news" title="뉴스와 감성" loaded={news}>
        {(data) => (
          <NewsBlock
            data={data}
            scoreAsOf={(score?.as_of_date as string | undefined) ?? null}
            market={String(stock.country ?? "") === "US" ? "US" : "KR"}
            nameNote={unmatchedNameNote(String(stock.country ?? ""), stock.name_ko as string | null)}
          />
        )}
      </Card>

      <Card lazy="events" id="events" title="실적·공시 일정" loaded={events}>
        {(data) => <EventsBlock data={data} />}
      </Card>

      <Card lazy="opinions" id="opinions" title="증권사 의견 · 그 증권사의 지난 성적" loaded={opinions}>
        {(data) => <OpinionsBlock data={data} />}
      </Card>
    </div>
  );
}

/**
 * 팩터 점수와 **그 근거**. 세 가지가 한 줄에 붙는다.
 *
 *   빠짐  구성 지표 중 값이 없어 평균에서 빠진 것
 *   치환  값이 없는데 **채운 값으로 대신 받은** 것 (docs/factors.md 3.5 — 미회복 회복 기간은 비슷하게 빠진 종목 최악·바닥 뒤 경과 중 나쁜 쪽).
 *         `raw` 에는 null 이 들어가고 `missing_fields` 에도 안 들어가므로,
 *         이것을 안 보여 주면 "값이 비었는데 왜 점수가 있나" 에 답할 수 없다
 *   리스크 원지표  Amihud·MAX·잔차 변동성. 성과지표 표에 없는 값이라
 *         2026-09-21 까지 **어느 화면에도 없었다** (docs/infra.md 25.93)
 */
function ScoreBlock({ score, factors, currency }: { score: NonNullable<Overview["score"]>; factors: Overview["factors"]; currency: string }) {
  const size = 180;
  const points = radarPoints(score.factor_scores, 70, size / 2);
  const sentiment = typeof score.sentiment_score === "number" ? score.sentiment_score : null;
  const byFactor = Object.fromEntries(factors.map((f) => [String(f.factor), f]));
  return (
    <div className="grid gap-4 sm:grid-cols-[auto_1fr]">
      <svg viewBox={`0 0 ${size} ${size}`} className="mx-auto h-44 w-44" role="img" aria-label="팩터 레이더">
        {[0.25, 0.5, 0.75, 1].map((k) => (
          <polygon
            key={k}
            points={radarPoints({ value: 100 * k, quality: 100 * k, growth: 100 * k, momentum: 100 * k, risk: 100 * k }, 70, size / 2)
              .map((p) => `${p.x},${p.y}`)
              .join(" ")}
            className="fill-none stroke-slate-200 dark:stroke-slate-700"
          />
        ))}
        <polygon points={points.map((p) => `${p.x},${p.y}`).join(" ")} className="fill-blue-500/25 stroke-blue-600" />
        {points.map((p) => (
          <text key={p.key} x={p.axisX} y={p.axisY} textAnchor="middle" dominantBaseline="middle" className="fill-slate-500 text-[9px]">
            {p.label} {p.score === null ? "-" : p.score.toFixed(0)}
          </text>
        ))}
      </svg>
      <div className="text-sm">
        <p>
          종합 <b className="text-lg">{typeof score.total_score === "number" ? score.total_score.toFixed(0) : "-"}</b>
          {typeof score.rank_in_market === "number" && <span className="ml-2 text-xs text-slate-500">시장 {score.rank_in_market}위</span>}
          {score.skip_reason ? <span className="ml-2 text-xs text-amber-700">{String(score.skip_reason)}</span> : null}
        </p>
        <div className="mt-2">
          <p className="text-xs text-slate-500">센티먼트 (종합 반영 {formatPercentNumber(score.sentiment_weight_used)}, 팩터와 따로 봅니다)</p>
          {sentiment === null ? (
            <p className="text-xs text-slate-400">{sentimentEmptyReason(score)}</p>
          ) : (
            <div className="relative mt-1 h-2 rounded bg-gradient-to-r from-blue-200 via-slate-100 to-red-200 dark:from-blue-900 dark:via-slate-800 dark:to-red-900">
              <span className="absolute -top-1 h-4 w-1 rounded bg-slate-900 dark:bg-white" style={{ left: `${(sentiment + 100) / 2}%` }} />
              <span className="absolute -top-5 text-xs" style={{ left: `calc(${(sentiment + 100) / 2}% - 1rem)` }}>
                {signedInt(sentiment)}
              </span>
            </div>
          )}
        </div>
        <table className="mt-3 w-full text-xs">
          <tbody>
            {FACTOR_LABELS.map(([key, label]) => {
              const f = byFactor[key];
              return (
                <tr key={key} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="whitespace-nowrap py-1 pr-2 text-slate-500">{label}</td>
                  <td className="py-1 pr-2">{typeof f?.score === "number" ? f.score.toFixed(0) : "-"}</td>
                  <td className="py-1 text-slate-400">
                    {f ? `${String(f.peer_group)} (${String(f.peer_size)}종목)` : "없음"}
                    {f?.missing.length ? ` · 빠짐: ${f.missing.map(metricLabel).join(", ")}` : ""}
                    {f ? (substitutedNote(f.raw) ? ` · 치환: ${substitutedNote(f.raw)}` : "") : ""}
                    {key === "risk" && f && priceRiskNote(f.raw, currency) ? ` · ${priceRiskNote(f.raw, currency)}` : ""}
                    {key === "risk" && f && riskSourceNote(f.raw) ? ` · ${riskSourceNote(f.raw)}` : ""}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * 매도 플래그의 **근거표**. 배치가 `sell_flags.rationale_data` 에 저장해 온 것을 편다.
 *
 * 2026-09-21 까지 화면은 요약 한 줄만 보여 줬다 — 저장은 하고 있었는데 읽지를 않았다.
 * CLAUDE.md 는 "근거에 쓴 **모든** 수치는 어느 행에서 왔는지 화면에서 펼쳐 볼 수 있어야
 * 한다" 고 못박는다. 보유 종목을 팔지 말지를 가르는 경고라면 더욱 그렇다.
 *
 * 기본은 접어 둔다. 플래그는 여럿일 수 있고 헤더가 길어지면 아래를 안 본다.
 */
function PositionBlock({ position, currency, stale }: { position: Row; currency: string; stale: boolean }) {
  const pnl = position.unrealized_pnl_krw as number | null;
  return (
    <section className="mb-4 rounded-xl border border-purple-200 bg-purple-50/40 p-3 text-sm dark:border-purple-900 dark:bg-purple-950/30">
      <div className="mb-1 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">내 보유{stale && <span className="ml-1 text-xs font-normal text-amber-700 dark:text-amber-300">(재계산 전 값)</span>}</h2>
        <span className="text-xs text-slate-500">기준 {String(position.price_date ?? "-")}</span>
      </div>
      <p>
        {/* **보유 금액은 반올림하지 않는다** (docs/infra.md 25.267). 재무제표용 `formatAmount` 는 억 단위 정수로 잘라
            1억 4,900만원이 "1억", 99,999,999원 손익이 그대로·100,000,001원이 "+1억" 이었다. 포트폴리오 화면·리포트와 같은 표기를 쓴다 */}
        {/* 소수점 보유를 반올림하지 않는다 — 0.4주가 "0주" 였다 (25.594, 교차검증) */}
        {qtyText(Number(position.quantity))}주 · 평균 {formatPrice(position.avg_price, currency)} · 평가 {money(position.market_value as number | null, currency)}
        {/* 색은 보이는 숫자를 따른다 — "0원" 이 빨강·파랑이던 것 (docs/infra.md 25.71·25.737) */}
        {typeof pnl === "number" && (
          <span className={`ml-2 ${pnlClass(pnl)}`}>
            {signedWon(pnl)}
          </span>
        )}
        {currency !== "KRW" && typeof position.unrealized_fx_pnl_krw === "number" && (
          <span className="ml-2 text-xs text-slate-500">
            (주가 {signedWon(position.unrealized_price_pnl_krw as number | null)} · 환율 {signedWon(position.unrealized_fx_pnl_krw as number | null)})
          </span>
        )}
      </p>
      <Link href="/portfolio" className="text-xs text-slate-500 underline">
        포트폴리오에서 보기
      </Link>
    </section>
  );
}

function PriceBlock({
  data,
  signal,
  signalRows,
  signalsState,
  signalAsOf,
  country,
  position,
  positionState,
  onHorizon,
  currency,
}: {
  data: SectionResult;
  signal: Row | null;
  signalRows: Row[];
  signalsState: "loading" | "error" | "ok";
  signalAsOf: string | null;
  country: string;
  position: Row | null;
  positionState: "ok" | "unread" | "stale";
  onHorizon: (h: string) => void;
  currency: string;
}) {
  const rows = data.rows as Array<Row & Bar>;
  const bars = useMemo(() => rows.map((r) => ({ date: r.date, open: r.open, high: r.high, low: r.low, close: r.close, volume: r.volume })), [rows]);
  const overlays = useMemo(() => overlaysFor(signal, position), [signal, position]);
  const last = rows.at(-1);
  return (
    <div>
      <p className="mb-1 text-sm">
        종가 <b>{formatPrice(last?.close, currency)}</b>
        <span className="ml-2 text-xs text-slate-500">
          {String(last?.date ?? "")} · 출처 {String(data.source ?? "-")} · {priceAdjustLabel(country)}
        </span>
      </p>
      {signalRows.length > 1 && (
        <div className="mb-1 flex gap-1 text-xs">
          {signalRows.map((r) => (
            <button
              key={String(r.horizon)}
              type="button"
              onClick={() => onHorizon(String(r.horizon))}
              className={`h-10 rounded border px-3 sm:h-auto sm:px-1.5 sm:py-0.5 ${signal?.horizon === r.horizon ? "border-blue-600 text-blue-700 dark:text-blue-300" : "border-slate-200 dark:border-slate-700"}`}
            >
              {HORIZON_LABEL[String(r.horizon)] ?? String(r.horizon)}
            </button>
          ))}
        </div>
      )}
      <PriceChart bars={bars} overlays={overlays} currency={currency} />
      <p className="mt-1 text-xs text-slate-500">
        {overlayNote(overlays, signalsState, signalAsOf, position, currency, formatPrice, positionState)}
      </p>
    </div>
  );
}

interface Judged {
  horizon: string;
  passed: boolean;
  failed_count: number;
  as_of: string;
  rows: unknown;
}

/**
 * 신호가 난 기간은 근거표, 안 난 기간은 판정표 (docs/signals.md 9장).
 * "왜 이 종목은 추천에 없나" 에 답한다 — 추천된 이유만이 아니라 안 된 이유도 확인돼야 완결이다.
 */
function ReferenceScore({ s }: { s: ReferenceScoreData }) {
  return (
    <div className="mt-2 text-sm">
      <p>
        <b>{formatNum(s.total)}</b>
        {s.rank && s.ranked ? <span className="ml-2 text-xs text-slate-500">유니버스 기준 {s.rank.toLocaleString()}위 상당/{s.ranked.toLocaleString()}</span> : null}
        <span className="ml-2 text-xs text-slate-500">기준 {s.as_of}</span>
      </p>
      <p className="mt-1 flex flex-wrap gap-x-3 text-xs text-slate-600 dark:text-slate-300">
        {FACTOR_LABELS.filter(([k]) => typeof s.factors?.[k] === "number").map(([k, label]) => (
          <span key={k}>{label} {formatNum(s.factors[k])}</span>
        ))}
      </p>
      <p className="mt-1 text-xs text-slate-400">유니버스 전 종목에 이 종목을 더해 같은 식으로 낸 값입니다(순위·추천에는 들어가지 않습니다).</p>
    </div>
  );
}

function SignalsBlock({ rows, checks, reason, currency, runNote, dateNote, reference = false }: {
  rows: Row[]; checks: Judged[]; reason: string | null; currency: string; runNote: string | null; dateNote: string | null;
  /** 유니버스 밖 종목의 참고 판정표 — 통과한 기간도 신호가 아니라 함께 보인다 (25.1019) */
  reference?: boolean;
}) {
  const signalled = new Set(rows.map((r) => String(r.horizon)));
  const missing = reference ? checks : checks.filter((c) => !signalled.has(c.horizon));
  return (
    <div className="space-y-3">
      {/* 건너뛴 실행은 사유 줄(reason)에 이미 들어 있다 — 신호가 있을 때만 따로 띄운다 (25.807) */}
      {rows.length > 0 && runNote ? <p className="text-xs text-amber-700 dark:text-amber-300">{runNote}</p> : null}
      {dateNote ? <p className="text-xs text-slate-500">{dateNote}</p> : null}
      {rows.length === 0 && reason ? <p className="text-xs text-slate-500">{reason}</p> : null}
      {rows.map((r) => {
        const tranches = (r.tranches as Array<{ step: number; ratio: number; price: number; amount: number | null }>) ?? [];
        return (
          <article key={String(r.horizon)} className="text-sm">
            <p className="font-medium">
              {HORIZON_LABEL[String(r.horizon)] ?? String(r.horizon)} · {String(r.signal_type)}
            </p>
            <p className="text-xs text-slate-600 dark:text-slate-300">{String(r.rationale_text)}</p>
            <p className="mt-1 text-xs">
              매수 구간 {formatPrice(r.buy_zone_low, currency)} ~ {formatPrice(r.buy_zone_high, currency)} · 목표 {formatPrice(r.target_price, currency)} · 손절{" "}
              {formatPrice(r.stop_price, currency)}
            </p>
            {tranches.length > 0 && (
              <p className="text-xs text-slate-500">
                분할: {tranches.map((t) => `${t.step}차 ${formatPrice(t.price, currency)} (${Math.round(t.ratio * 100)}%)`).join(" → ")}
              </p>
            )}
            <CriteriaTable rows={parseCriteria(String(r.rationale_data ?? ""))} caption={`신호 계산일 ${String(r.as_of_date)}`} docPath="docs/signals.md" staleAfterDays={STALE_AFTER_DAYS} />
          </article>
        );
      })}
      {missing.length > 0 ? (
        <div className="border-t border-slate-100 pt-2 dark:border-slate-800">
          <p className="text-xs font-medium text-slate-600 dark:text-slate-300">{reference ? "참고 판정표" : "왜 신호가 없나"}</p>
          {missing.map((c) => (
            <article key={c.horizon} className="mt-1 text-xs">
              <p>
                {HORIZON_LABEL[c.horizon] ?? c.horizon} · {reference && c.passed ? "조건 모두 충족(참고 — 신호 아님)" : `탈락 ${c.failed_count}개 기준`}
              </p>
              <CriteriaTable rows={parseCriteria(JSON.stringify({ criteria: c.rows }))} caption={`판정일 ${c.as_of}`} docPath="docs/signals.md" staleAfterDays={STALE_AFTER_DAYS} />
            </article>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ValuationBlock({ band, factors, currency }: { band: Row; factors: Overview["factors"]; currency: string }) {
  const pos = bandPositions(band);
  const value = factors.find((f) => f.factor === "value");
  const marks: Array<[keyof NonNullable<typeof pos>, string]> = [
    ["p20", "20%"],
    ["p30", "30%"],
    ["p50", "50%"],
    ["p80", "80%"],
  ];
  return (
    <div className="text-sm">
      <p>
        현재 PBR <b>{formatNum(band.current_value)}배</b>
        {typeof band.band_rank === "number" && <span className="ml-2 text-xs text-slate-500">자기 3년 계열의 {band.band_rank.toFixed(0)}% 지점</span>}
      </p>
      {band.skip_reason ? <p className="text-xs text-amber-700">{String(band.skip_reason)}</p> : null}
      {pos && band.p50 !== null && (
        <div className="relative my-6 h-2 rounded bg-slate-100 dark:bg-slate-800">
          {pos.p20 !== null && pos.p80 !== null && (
            <span className="absolute h-2 rounded bg-slate-300 dark:bg-slate-600" style={{ left: `${pos.p20}%`, width: `${pos.p80 - pos.p20}%` }} />
          )}
          {marks.map(([k, label]) =>
            pos[k] === null ? null : (
              <span key={k} className="absolute top-3 -translate-x-1/2 text-[10px] text-slate-500" style={{ left: `${pos[k]}%` }}>
                {label} {formatNum(band[k])}
              </span>
            ),
          )}
          {pos.current !== null && (
            <span className="absolute -top-4 -translate-x-1/2 text-[10px] font-semibold text-blue-700 dark:text-blue-300" style={{ left: `${pos.current}%` }}>
              ▼ 현재
            </span>
          )}
        </div>
      )}
      <table className="mt-2 w-full text-xs">
        <tbody className="[&_td]:py-1 [&_td:first-child]:pr-2 [&_td:first-child]:text-slate-500">
          <tr>
            <td>업종 안 위치</td>
            <td>
              {typeof band.peer_percentile === "number"
                ? `PBR 낮은 쪽에서 ${band.peer_percentile.toFixed(0)}% (${String(band.peer_group)}, ${String(band.peer_size)}종목)`
                : "데이터 없음"}
            </td>
          </tr>
          <tr>
            <td>표본</td>
            <td>
              {String(band.sample)}거래일 · 종가 {String(band.price_date ?? "-")} · 자본총계
              {/* 연결이 없는 회사는 별도 자본총계로 밴드를 낸다 — 어느 쪽인지 밝힌다 (docs/infra.md 25.334) */}
              {band.equity_consolidated === 0 ? "(별도)" : band.equity_consolidated === 1 ? "(연결)" : ""} 접수 {String(band.equity_report_date ?? "-")}
            </td>
          </tr>
          <tr>
            <td>밸류 원지표</td>
            <td>
              {value
                ? `E/P ${formatPct(value.raw.ep)} · B/P ${formatPct(value.raw.bp)} · S/P ${formatPct(value.raw.sp)}` +
                  ` · D/P ${formatPct(value.raw.dividend_yield)} (기준 ${String(value.as_of_date)})`
                : "데이터 없음"}
            </td>
          </tr>
          {/* 분모(시가총액)를 어느 날 값에서 어떻게 옮겼는지 — 밸류 넷의 분모 시점 (docs/factors.md 3.1, infra 25.954) */}
          {value && (
            <tr>
              <td>시가총액(분모)</td>
              <td>{marketCapNote(value.raw as Record<string, unknown>, currency)}</td>
            </tr>
          )}
        </tbody>
      </table>
      <p className="mt-1 text-[11px] text-slate-400">
        상장주식수는 현재 값 하나로 과거 PBR 을 계산합니다. 증자·감자가 있었으면 과거 밴드가 어긋납니다.
      </p>
    </div>
  );
}

/**
 * 과거 성과. **지표를 행, 기간(1·3·5년)을 열**로 둔다. 전에는 기간이 행, 지표 여덟이 열이라
 * 폰에서 옆으로 넘쳤다(2026-09-18 "표가 옆으로 넘친다"). 열이 셋이면 폰 폭에 들어간다.
 */
function MetricsBlock({ rows }: { rows: Row[] }) {
  const lines: Array<[string, (r: Row) => string, ((r: Row) => string | undefined)?]> = [
    ["CAGR", (r) => formatPct(r.cagr)],
    ["MDD", (r) => formatPct(r.mdd), (r) => (r.mdd_peak_date && r.mdd_trough_date ? `${String(r.mdd_peak_date)} → ${String(r.mdd_trough_date)}` : undefined)],
    ["회복", (r) => recoveryText(r)],
    ["변동성", (r) => formatPct(r.volatility_ann)],
    ["샤프", (r) => formatNum(r.sharpe)],
    ["소르티노", (r) => formatNum(r.sortino)],
    ["베타", (r) => formatNum(r.beta)],
  ];
  return (
    <div>
      <table className="w-full whitespace-nowrap text-xs">
        <thead className="text-slate-500">
          <tr>
            <th className="py-1 pr-3 text-left font-medium">지표</th>
            {rows.map((r) => (
              <th key={String(r.window)} className="py-1 pr-3 text-right font-medium">
                {String(r.window)}
                {/* 몇 거래일로 계산했나 — 4년치로 낸 값도 "5Y" 로만 보였다 (docs/infra.md 25.590) */}
                {typeof r.data_points === "number" ? <span className="block text-[10px] font-normal text-slate-400">{r.data_points}일</span> : null}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lines.map(([label, value, title]) => (
            <tr key={label} className="border-t border-slate-100 dark:border-slate-800">
              <td className="py-1 pr-3 text-slate-500">{label}</td>
              {rows.map((r) => (
                <td key={String(r.window)} className="py-1 pr-3 text-right tabular-nums" title={title?.(r)}>
                  {value(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {/* 값이 모두 비었으면 "계산하지 않음" 이라고 말한다 — `-` 만 늘어놓으면 "값 없음" 과 가를 수 없었다 (25.590, 감사) */}
      {rows.filter((r) => [r.cagr, r.mdd, r.volatility_ann, r.sharpe].every((v) => v === null || v === undefined)).map((r) => (
        <p key={String(r.window)} className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">
          {String(r.window)}: 표본이 모자라 계산하지 않았습니다{typeof r.data_points === "number" ? ` (${r.data_points}거래일)` : ""}
        </p>
      ))}
      {/* 기준 지수는 베타를 낸 기간에만 적힌다(25.684) — 첫 행(1Y)만 보면 3Y 베타가 있어도 "-" 였다 (25.686) */}
      <p className="mt-1 text-[11px] text-slate-400">
        일간 수익률·연 252일 기준. 베타 {String(rows.find((r) => r.benchmark)?.benchmark ?? "-")} 대비, 무위험수익률 {formatPct(rows[0]?.risk_free_rate_used, 2)}.
      </p>
    </div>
  );
}

/** 폰에서 보일 최근 열 수. 연도·분기처럼 옆으로 늘어나는 표에 쓴다. 넓은 화면은 전부 보인다 */
function recentOnly(index: number, total: number, keep: number): string {
  return index < total - keep ? "hidden sm:table-cell" : "";
}

function FinancialsBlock({ rows }: { rows: Row[] }) {
  const currency = String(rows.at(-1)?.currency ?? "KRW");
  const lines: Array<[string, string]> = [
    ["revenue", "매출"],
    ["operating_income", "영업이익"],
    ["net_income", "순이익"],
    ["total_assets", "자산"],
    ["total_liabilities", "부채"],
    ["total_equity", "자본"],
  ];
  // 폰은 최근 3년만. 다섯 해를 다 놓으면 옆으로 넘쳤다 (2026-09-18)
  const keep = 3;
  return (
    <div className="overflow-x-auto">
      <table className="w-full whitespace-nowrap text-xs">
        <thead className="text-left text-slate-500">
          <tr>
            <th className="py-1 pr-3 font-medium">항목</th>
            {rows.map((r, i) => (
              <th key={String(r.fiscal_year)} className={`py-1 pr-3 text-right font-medium ${recentOnly(i, rows.length, keep)}`}>
                {String(r.fiscal_year)}
                {r.consolidated === 0 ? "*" : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lines.map(([key, label]) => (
            <tr key={key} className="border-t border-slate-100 dark:border-slate-800">
              <td className="py-1 pr-3 text-slate-500">{label}</td>
              {rows.map((r, i) => (
                <td key={String(r.fiscal_year)} className={`py-1 pr-3 text-right tabular-nums ${recentOnly(i, rows.length, keep)}`}>
                  {formatAmount(r[key], String(r.currency ?? currency))}
                </td>
              ))}
            </tr>
          ))}
          <tr className="border-t border-slate-100 text-slate-400 dark:border-slate-800">
            <td className="py-1 pr-3">접수일</td>
            {rows.map((r, i) => (
              <td key={String(r.fiscal_year)} className={`py-1 pr-3 text-right ${recentOnly(i, rows.length, keep)}`}>
                {String(r.report_date)}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
      <p className="mt-1 text-[11px] text-slate-400">
        {rows.length > keep ? <span className="sm:hidden">폰에서는 최근 {keep}년만 보입니다. </span> : null}
        출처 {String(rows.at(-1)?.source ?? "-")} · {String(rows.at(-1)?.accounting_standard ?? "회계기준 미표기")} · 연결 기준, *는 별도. 빈 칸(-)은 원본에 값이 없는 것입니다.
      </p>
    </div>
  );
}

function NewsBlock({ data, scoreAsOf, market, nameNote }: { data: SectionResult; scoreAsOf: string | null; market: "KR" | "US"; nameNote?: string | null }) {
  const articles = data.articles as Row[];
  const trend = data.trend as Row[];
  const latest = data.latest as Row | null;
  const points = trend.filter((t) => typeof t.sentiment === "number");
  const w = 300;
  const h = 60;
  const path = points
    .map((t, i) => {
      const x = points.length === 1 ? w / 2 : (i / (points.length - 1)) * w;
      const y = h / 2 - ((t.sentiment as number) / 100) * (h / 2);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  // 묵은 감성은 그렇다고 먼저 말한다 (25.590). 나이는 **점수 기준일에서** 잰다 — 배치(`jobs/scores`)가 그렇게 잰다. 오늘로 재면 연휴 뒤
  // 배치가 돌기 전에 방금 점수에 쓰인 감성에도 "쓰지 않습니다" 가 붙었다 (25.592, 교차검증: 추석 9/24~25). 점수가 없으면 오늘
  // 단, **점수 자체가 묵었으면**(유니버스에서 빠짐·배치 멈춤) 점수 기준일로 재면 7월 감성도 "새것" 이 된다 (25.594, 교차검증).
  // 점수 기준일이 오늘보다 SCORE_STALE_DAYS 넘게 앞서면 오늘에서 잰다. 국내 최장 연휴가 11일(2017-09-29 → 10-10, batch/services/metrics.py)
  // 이라 그보다 길게 둔다 — 7일로 두었더니 긴 연휴 뒤 첫날에 오경보가 되살아났다 (25.596, 교차검증)
  // 사용자의 오늘(한국) — UTC 날짜면 00~09시 KST 에 하루 일러 3일 넘은 감성 경고가 빠졌다 (25.671, 감사. 25.236·25.207 과 같은 자리)
  // 그 종목 시장의 오늘 — 미국 점수·감성 기준일은 뉴욕 날짜다. 한국 날짜로 재면 KST 00~13시에 하루 어긋났다 (25.675, 교차검증)
  const 오늘 = localDate(market, new Date());
  const 점수묵음 = scoreAsOf ? (Date.parse(`${오늘}T00:00:00Z`) - Date.parse(`${scoreAsOf.slice(0, 10)}T00:00:00Z`)) / 86_400_000 > SCORE_STALE_DAYS : true;
  const 묵음 = latest
    ? staleSentimentNote(latest.as_of_date as string | null, 점수묵음 ? 오늘 : scoreAsOf!.slice(0, 10))
    : null;
  return (
    <div className="text-sm">
      {/* 이름이 짧아 매칭하지 않는 종목은 그 까닭을 먼저 말한다 (25.747) */}
      {nameNote ? <p className="mb-1 text-xs font-medium text-amber-700 dark:text-amber-300">{nameNote}</p> : null}
      {묵음 ? <p className="mb-1 text-xs font-medium text-amber-700 dark:text-amber-300">{묵음}</p> : null}
      {latest ? (
        <p className={`text-xs ${묵음 ? "text-slate-400" : "text-slate-600 dark:text-slate-300"}`}>
          감성 {typeof latest.sentiment === "number" ? <b>{signedInt(latest.sentiment)}</b> : sentimentNullReason(latest.article_count)} · 30일 기사 {String(latest.article_count)}건 (긍정{" "}
          {String(latest.positive_count)} · 부정 {String(latest.negative_count)}) · 7일 변화 {typeof latest.delta_7d === "number" ? signedInt(latest.delta_7d) : "-"} · 채점 {String(latest.method)}
        </p>
      ) : (
        <p className="text-xs text-slate-400">감성 집계 없음 — 기사는 있지만 아직 채점·집계되지 않았습니다</p>
      )}
      {points.length >= 2 ? (
        <svg viewBox={`0 0 ${w} ${h}`} className="my-2 h-16 w-full" preserveAspectRatio="none" role="img" aria-label="감성 추이">
          <line x1="0" x2={w} y1={h / 2} y2={h / 2} className="stroke-slate-200 dark:stroke-slate-700" />
          <path d={path} className="fill-none stroke-blue-600" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
        </svg>
      ) : (
        <p className="my-2 text-xs text-slate-400">추이 데이터 없음 — 점수가 있는 날이 2일 이상 쌓이면 그립니다</p>
      )}
      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {articles.map((a) => (
          <li key={String(a.id)} className="flex items-baseline gap-2 py-2.5 text-xs sm:py-1.5">
            <span
              className={`w-10 shrink-0 text-right tabular-nums ${
                typeof a.score !== "number" ? "text-slate-300" : a.score > 0.05 ? "text-red-600" : a.score < -0.05 ? "text-blue-600" : "text-slate-400"
              }`}
            >
              {typeof a.score === "number" ? a.score.toFixed(2) : "미채점"}
            </span>
            <a href={String(a.url)} target="_blank" rel="noopener noreferrer" className="flex-1 hover:underline">
              {String(a.title)}
            </a>
            <span className="shrink-0 text-slate-400">{shortTime(String(a.published_at)).slice(0, 12)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-[11px] text-slate-400">제목·주소·시각만 저장합니다. 점수 −1~+1, 종목 감성 −100~+100 (반감기 7일).</p>
    </div>
  );
}

/**
 * 분기·반기 재무. **보고서에 적힌 금액 그대로** 보이고 빼거나 더하지 않는다.
 * 손익은 그 분기 3개월 값이다 — 반기 보고서의 값도 4~6월 값이다(2026-09-18 확인, docs/data-sources.md 1.3).
 * 그래서 열 이름을 보고서 이름("반기")이 아니라 분기("2분기(4~6월)")로 쓴다.
 */
function QuarterlyBlock({ rows }: { rows: Row[] }) {
  const currency = String(rows.at(-1)?.currency ?? "KRW");
  const lines: Array<[string, string]> = [
    ["revenue", "매출"],
    ["operating_income", "영업이익"],
    ["net_income", "순이익"],
  ];
  // 폰은 최근 네 분기(한 해치)만, 열 이름도 "2026 2분기" 까지만. 달 범위는 넓은 화면에서 붙인다
  const keep = 4;
  return (
    <div className="overflow-x-auto">
      <table className="w-full whitespace-nowrap text-xs">
        <thead className="text-left text-slate-500">
          <tr>
            <th className="py-1 pr-3 font-medium">항목</th>
            {rows.map((r, i) => {
              const label = QUARTER_LABEL[String(r.report_code)] ?? REPORT_LABEL[String(r.report_code)] ?? String(r.report_code);
              const cut = label.indexOf("(");
              return (
                <th key={`${r.fiscal_year}-${r.report_code}`} className={`py-1 pr-3 text-right font-medium ${recentOnly(i, rows.length, keep)}`}>
                  {String(r.fiscal_year)} {cut > 0 ? label.slice(0, cut) : label}
                  {cut > 0 ? <span className="hidden sm:inline">{label.slice(cut)}</span> : null}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {lines.map(([key, label]) => (
            <tr key={key} className="border-t border-slate-100 dark:border-slate-800">
              <td className="py-1 pr-3 text-slate-500">{label}</td>
              {rows.map((r, i) => (
                <td key={`${r.fiscal_year}-${r.report_code}`} className={`py-1 pr-3 text-right tabular-nums ${recentOnly(i, rows.length, keep)}`}>
                  {formatAmount(r[key], String(r.currency ?? currency))}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[11px] text-slate-400">
        {rows.length > keep ? <span className="sm:hidden">폰에서는 최근 {keep}개 분기만 보입니다. </span> : null}
        보고서에 적힌 금액 그대로입니다. 손익은 그 분기 3개월 값입니다(반기 보고서도 4~6월 값). 4분기는 사업보고서에만 있어 싣지 않았습니다.
        {/* **어느 재무 기준·어디서 왔는지** (docs/infra.md 25.334). 연간 표에는 있고 분기 표에는 없었다 */}
        {" "}{rows.some((r) => Number(r.consolidated) === 0) ? "별도 재무(연결 재무를 내지 않는 회사)" : "연결 재무"} ·
        출처 {[...new Set(rows.map((r) => String(r.source ?? "")).filter(Boolean))].join(", ") || "-"}
        {rows.at(-1)?.fetched_at ? ` · 받은 때 ${userDateOf(String(rows.at(-1)?.fetched_at))}` : ""}
      </p>
    </div>
  );
}

/**
 * 배당 이력 (docs/data-sources.md 1.1).
 *
 * **연속성·증감은 총액으로 읽는다.** 주당배당금은 액면분할이 있으면 해마다 비교할 수 없다
 * (삼성전자 2018년 50:1 분할로 42,500 → 1,416원). 총액을 먼저 보이고 주당은 곁들인다.
 */
function DividendsBlock({ rows, currency }: { rows: Row[]; currency: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full whitespace-nowrap text-xs">
        <thead className="text-left text-slate-500">
          <tr>
            <th className="py-1 pr-3 font-medium">사업연도</th>
            <th className="py-1 pr-3 text-right font-medium">배당총액</th>
            <th className="py-1 pr-3 text-right font-medium">주당</th>
            <th className="py-1 pr-3 text-right font-medium">배당성향</th>
            <th className="py-1 pr-3 text-right font-medium">시가배당률</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={String(r.fiscal_year)} className="border-t border-slate-100 dark:border-slate-800">
              <td className="py-1 pr-3">
                {String(r.fiscal_year)}
                {/* 폰에서는 보고서 연도와 배당성향 기준을 뺀다 — 숫자 넷만 남기면 폭에 들어간다 */}
                <span className="ml-1 hidden text-slate-400 sm:inline">({String(r.report_year)} 보고서)</span>
              </td>
              <td className="py-1 pr-3 text-right tabular-nums">{formatAmount(r.cash_dividend_total, currency)}</td>
              <td className="py-1 pr-3 text-right tabular-nums">
                {/* 종목 통화로 적는다 — 미국은 SEC 달러 배당이다 (docs/infra.md 25.381. 예전에는 "0.96원" 이었다) */}
                {typeof r.dps_common === "number" ? money(r.dps_common, currency) : "-"}
              </td>
              <td className="py-1 pr-3 text-right tabular-nums">
                {typeof r.payout_ratio === "number" ? `${r.payout_ratio.toFixed(1)}%` : "-"}
                {r.payout_basis ? <span className="ml-1 hidden text-slate-400 sm:inline">{String(r.payout_basis)}</span> : null}
              </td>
              <td className="py-1 pr-3 text-right tabular-nums">
                {typeof r.yield_common === "number" ? `${r.yield_common.toFixed(2)}%` : "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[11px] text-slate-400">
        증감은 <b>배당총액</b>으로 봅니다. 주당배당금은 액면분할이 있으면 해마다 비교할 수 없습니다.
        {dividendSourceLine(rows, userTimeOf) ? <> {dividendSourceLine(rows, userTimeOf)}</> : null}
      </p>
    </div>
  );
}

/**
 * 증권사 의견 (docs/brokers.md, 25.995). 의견마다 **그 증권사가 지난 1년 얼마나 맞혔나** 를 붙인다 — 목표가만 보면 늘 낙관적인
 * 증권사와 신중한 증권사를 가를 수 없다. 출처는 KIS 투자의견, 성적은 우리 시세로 매주 계산한 것이다.
 */
function OpinionsBlock({ data }: { data: SectionResult }) {
  const opinions = data.opinions as Row[];
  const stats = (data.stats ?? {}) as Record<string, BrokerStat>;
  return (
    <div className="text-xs">
      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {opinions.map((o, i) => (
          <li key={i} className="py-1">
            <span className="text-slate-500">{String(o.date)}</span> <b>{String(o.broker)}</b> {String(o.opinion ?? "-")}
            {o.target_price ? ` · 목표가 ${Number(o.target_price).toLocaleString("ko-KR")}원` : ""}
            <div className="text-slate-500">{brokerLine(stats[String(o.broker)])}</div>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-slate-400">
        출처 한국투자증권 KIS 투자의견 · 성적은 의견 다음 거래일 종가 기준 60거래일 지수(코스피·코스닥) 대비, 목표가 터치는 120거래일 안 고가 ·
        기준일 {String(Object.values(stats)[0]?.as_of ?? "-")}
      </p>
    </div>
  );
}

function EventsBlock({ data }: { data: SectionResult }) {
  const earnings = data.earnings as Row[];
  const disclosures = data.disclosures as Row[];
  return (
    <div className="grid gap-3 text-xs sm:grid-cols-2">
      <div>
        <p className="mb-1 font-medium">다가오는 일정</p>
        {earnings.length === 0 ? (
          <p className="text-slate-400">데이터 없음</p>
        ) : (
          <ul>
            {earnings.map((e, i) => (
              <li key={i} title={e.note ? String(e.note) : undefined}>
                {String(e.scheduled_date)} {String(e.event_type)}{" "}
                <span className={e.is_confirmed ? "text-slate-500" : "text-slate-400"}>({eventOrigin(e)})</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <p className="mb-1 font-medium">최근 공시</p>
        {disclosures.length === 0 ? (
          <p className="text-slate-400">데이터 없음</p>
        ) : (
          <ul>
            {disclosures.map((d, i) => (
              <li key={i}>
                {String(d.disclosed_at)}{" "}
                {d.url ? (
                  <a href={String(d.url)} target="_blank" rel="noopener noreferrer" className="hover:underline">
                    {String(d.title)}
                  </a>
                ) : (
                  String(d.title)
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
