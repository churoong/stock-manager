"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import FlagCriteria from "@/components/FlagCriteria";
import { bodyOf, readJson } from "@/lib/http";
import { confirmNeeded, confirmPrompt, CONFIRM_CANCEL, type ConfirmKind } from "@/lib/confirm";
import { dividendBody, readAmount, unreadableAmount, estimateWithholding, recalcPending, money, pct, pctSuffix, LIST_LIMIT, lookthroughLines, type Lookthrough, pnlClass, pctClass, qtyText, signedInt, signedWon, tradeBody, withholdingRate, won } from "@/lib/portfolio";
type DividendTaxes = { kr_dividend_pct?: number | null; us_dividend_pct?: number | null };

import { rateText } from "@/lib/recommend";
import { OUTCOME_LABEL, OUTCOME_TONE, REVIEW_LIMIT, factorEntries, reviewPnlText, sampleNote, type ReviewRow, type ReviewStat } from "@/lib/review";

/**
 * 포트폴리오 화면 (docs/portfolio.md 5장). 탭: [보유] [매매 기록] [배당] [실현손익] [복기].
 *
 * [복기] 는 Step 15 (docs/review.md). 설계는 별도 화면이었지만 상단 탭이 이미 여섯이라 여기 탭으로 둔다.
 * 입력이 실현손익과 같고(청산된 매수), 실현손익 옆에서 "왜 샀고 어떻게 됐나" 를 보는 흐름이 자연스럽다.
 *
 * 숫자는 전부 배치가 저장한 값이다. 화면에서 더하거나 나누지 않는다.
 * 원본을 바꾼 뒤 재계산이 끝나기 전에는 "계산 중" 띠를 띄운다.
 */

type Stock = { id: number; ticker: string; name: string; country: string; currency: string; market: string; asset_type?: string };

interface Position {
  stock_id: number; ticker: string; name: string; country: string; currency: string; sector: string | null;
  quantity: number; avg_price: number; avg_fx: number; cost_krw: number; first_buy_date: string; horizon: string | null;
  price_date: string | null; close: number | null; fx_now: number | null; market_value: number | null;
  market_value_krw: number | null; unrealized_pnl: number | null; unrealized_pnl_krw: number | null;
  unrealized_price_pnl_krw: number | null; unrealized_fx_pnl_krw: number | null; weight_pct: number | null;
}

interface Lot {
  id: number; ticker: string; name: string; currency: string; quantity: number; buy_price: number; sell_price: number;
  buy_fx: number; sell_fx: number; realized_pnl: number; realized_pnl_krw: number; price_pnl_krw: number;
  fx_pnl_krw: number; fee_total: number; tax_total: number; cost_estimated: number; holding_days: number;
  sell_date: string;
}

interface Trade {
  id: number; ticker: string; name: string; country: string; side: string; trade_date: string; price: number;
  quantity: number; currency: string; fx_rate: number; fx_rate_source: string; fee: number | null; tax: number | null;
  horizon: string | null; memo: string | null; snapshot_as_of: string | null; score_at_trade: number | null;
  signal_type_at_trade: string | null; factor_scores_at_trade: string | null;
}

interface Dividend {
  id: number; ticker: string; name: string; pay_date: string; gross_amount: number; tax: number; net_amount: number;
  currency: string; fx_rate: number; fx_rate_source: string; memo: string | null;
}

interface ReviewData {
  reviews: ReviewRow[];
  stats: ReviewStat[];
  notice: string | null;
}

interface SellFlag {
  id: number; stock_id: number; level: "red" | "yellow" | "green"; reason_code: string; rationale_text: string;
  first_seen_date: string; dismissed_at: string | null; as_of_date: string;
  /** 근거표 JSON. 질의(`ACTIVE_FLAGS`)가 처음부터 읽고 있었는데 그리지 않았다 (docs/infra.md 25.262) */
  rationale_data?: string | null;
}

const FLAG_STYLE: Record<string, string> = {
  red: "bg-rose-50 text-rose-800 dark:bg-rose-950 dark:text-rose-200",
  yellow: "bg-amber-50 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  green: "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
};
const FLAG_MARK: Record<string, string> = { red: "적", yellow: "황", green: "녹" };

interface Summary {
  as_of: string | null; stale: boolean; has_trades: boolean; recalc_stuck?: string | null;
  totals: Record<string, number | boolean | string | null> | null;
  allocation: {
    by_sector: Record<string, number>;
    by_currency: Record<string, number>;
    /** 배치가 설정값으로 고른 업종 상한과 넘은 업종 (25.217). 옛 요약에는 없다 */
    max_sector_pct?: number | null;
    sectors_over_cap?: string[];
    /** 계좌 전체 노출 — ETF 를 펼친 것 (25.1002). 보유 ETF 가 없거나 옛 요약이면 없다 */
    lookthrough?: Lookthrough | null;
  } | null;
  metrics: Record<string, number | string | null> | null;
  upcoming: Array<{ ticker: string; name: string; kind: string; deadline: string; d_day: number; basis?: string }>;
  warnings: string[]; positions: Position[]; lots: Lot[]; flags: SellFlag[]; lots_truncated?: boolean;
}

const HORIZON: Record<string, string> = { short: "단기", mid: "중기", long: "장기" };
// 자동 환율은 그날 이하 가장 최근 종가(7일 안)다 — "그날 종가" 는 체결 당일 밤 입력에서 틀렸다 (25.591)
const FX_SOURCE: Record<string, string> = { auto: "자동: 그날 이하 최근 종가", manual: "직접 입력", none: "" };


/** 실적 일정의 근거 (25.910). 옛 요약 행에는 `basis` 가 없다 — 그때는 법정 기한이었다 */
export const UPCOMING_BASIS: Record<string, string> = {
  yahoo: "야후 예정",
  yahoo_range: "야후 예정 구간",
  legal: "법정 기한",
};

export default function PortfolioView() {
  const [tab, setTab] = useState<"positions" | "trades" | "dividends" | "lots" | "review">("positions");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [dividends, setDividends] = useState<Dividend[]>([]);
  const [review, setReview] = useState<ReviewData | null>(null);
  // 배당 폼이 원천징수를 채우는 데 쓴다 (docs/infra.md 25.127). 없으면 못 채울 뿐이라
  // 실패를 화면 오류로 올리지 않는다 — 이 화면의 원본은 매매와 배당이다
  const [taxes, setTaxes] = useState<DividendTaxes | null>(null);
  // 설정을 **못 읽은 것**과 세율이 **없는 것**을 가른다 (docs/infra.md 25.591, 감사). 예전에는 둘 다 null 이라 읽기 실패에도
  // "설정에 세율이 없어 … 비워 두면 0 으로 기록" 이 떠, 세후 = 세전인 배당이 원본에 영구히 남을 수 있었다
  const [taxesUnread, setTaxesUnread] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // 목록이 상한에서 잘렸으면 그렇다고 말한다 (25.591)
  const [truncatedNote, setTruncatedNote] = useState<string | null>(null);
  // **가장 늦게 보낸 불러오기만 싣는다** (docs/infra.md 25.591, 감사). 30초 폴링과 저장 뒤 불러오기가 겹쳐 늦게 온 옛 응답이
  // 방금 지운 매매를 되살리거나 "계산 중" 을 덮어 옛 합계가 최신처럼 보였다(25.577·25.586 과 같은 모양)
  const 불러오기번호 = useRef(0);
  const load = useCallback(async () => {
    const 번호 = ++불러오기번호.current;
    // 네 경로를 **따로** 읽는다. 하나가 실패해도 나머지는 그린다.
    //
    // 겪은 것(2026-09-17): 복기 경로를 붙이면서 응답 넷을 한 try 안에서 파싱했더니,
    // 배포 직후 /api/review 가 아직 없어 JSON 파싱이 터졌고 **매매 기록·보유가 통째로
    // 비어 보였다.** 한 화면이 네 경로에 걸려 있으면 가장 약한 하나가 전체를 끈다.
    const [p, t, d, r, st] = await Promise.all([
      readJson("/api/portfolio", undefined, { cache: true }),
      readJson("/api/trades", undefined, { cache: true }),
      readJson("/api/dividends", undefined, { cache: true }),
      readJson("/api/review", undefined, { cache: true }),
      readJson("/api/settings", undefined, { cache: true }),
    ]);

    if (번호 !== 불러오기번호.current) return;
    const 잘림 = [
      (t.data as { truncated?: boolean } | null)?.truncated ? "매매 기록" : null,
      (d.data as { truncated?: boolean } | null)?.truncated ? "배당" : null,
      (p.data as { lots_truncated?: boolean } | null)?.lots_truncated ? "실현손익 묶음" : null,
    ].filter(Boolean);
    // 셋 중 하나라도 못 읽었으면 잘림 안내를 건드리지 않는다 — 옛 목록은 남는데 안내만 지워졌다 (25.594)
    // 복기는 한도가 다르다(300, 25.936) — 따로 한 줄
    const 복기잘림 = (r.data as { truncated?: boolean } | null)?.truncated
      ? `복기 목록은 최근 ${REVIEW_LIMIT}건만 보입니다 — 복기 통계는 전체 기준입니다`
      : null;
    if (t.ok && d.ok && p.ok && r.ok) {
      setTruncatedNote(
        [잘림.length ? `${잘림.join("·")} 목록이 너무 길어 최근 ${LIST_LIMIT.toLocaleString()}건만 보입니다 — 합계는 요약(전체)을 봅니다` : null, 복기잘림]
          .filter(Boolean)
          .join(" · ") || null,
      );
    }

    // `?.` 가 필요하다: readJson 은 **본문이 빈 200** 에도 ok 를 주고 data 는 null 이다.
    // 그때 `(t.data as {...}).trades` 는 던지고, 이 함수 전체가 멈춰 **아래 setError 까지
    // 못 간다** — 화면이 왜 비었는지도 못 말한다. 2026-09-17 에 겪은 그 모양이다.
    // 다른 화면(AlertCenter·ReportView·ScreenerForm)은 모두 막고 있었고 여기만 뚫려 있었다
    if (p.ok) setSummary(p.data as Summary);
    if (t.ok) setTrades(((t.data as { trades?: Trade[] } | null)?.trades ?? []) as Trade[]);
    if (d.ok) setDividends(((d.data as { dividends?: Dividend[] } | null)?.dividends ?? []) as Dividend[]);
    setReview(
      r.ok
        ? (r.data as ReviewData)
        : { reviews: [], stats: [], notice: r.error ?? "복기를 불러오지 못했습니다" },
    );

    setTaxes(st.ok ? ((st.data as { settings?: { taxes?: DividendTaxes } } | null)?.settings?.taxes ?? null) : null);
    setTaxesUnread(!st.ok);

    // 매매·배당은 이 화면의 원본이라 실패를 숨기지 않는다. 복기는 제 탭 안에서만 알린다
    const failed = [
      p.ok ? null : `손익 ${p.error}`,
      t.ok ? null : `매매 기록 ${t.error}`,
      d.ok ? null : `배당 ${d.error}`,
    ].filter(Boolean);
    setError(failed.length ? failed.join(" · ") : null);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 계산 중이면 30초마다 다시 본다. 배치는 1~2분 걸린다
  useEffect(() => {
    // 멈춘 것으로 판정되면 더 부르지 않는다 — 끝없이 다섯 경로를 다시 읽었다 (25.554)
    if (!summary?.stale || summary.recalc_stuck) return;
    // 화면이 뒤에 있으면(다른 앱·다른 탭) 읽지 않는다 — DB 읽기 절약 (25.1032)
    const timer = setInterval(() => document.visibilityState !== "hidden" && void load(), 30_000);
    return () => clearInterval(timer);
  }, [summary?.stale, summary?.recalc_stuck, load]);

  const afterSave = (recalc?: { dispatched: boolean; reason?: string }) => {
    setNotice(recalc?.dispatched ? "저장했습니다. 1~2분 뒤 손익에 반영됩니다" : `저장했습니다. ${recalc?.reason ?? ""}`);
    void load();
  };

  /** [＋ 매매 입력]: 기록 탭으로 가서 입력 칸으로 내려간다 */
  const openTradeForm = () => {
    setTab("trades");
    window.setTimeout(() => document.getElementById("trade-form")?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };

  return (
    <div>
      {error ? <p className="mb-2 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-950 dark:text-rose-300">{error}</p> : null}
      {truncatedNote ? <p className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">{truncatedNote}</p> : null}
      {notice ? <p className="mb-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">{notice}</p> : null}
      {recalcPending(summary) && summary?.recalc_stuck ? (
        <p className="mb-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:bg-rose-950 dark:text-rose-200">
          {summary.recalc_stuck}. 아래 손익·보유는 마지막 계산({summary.as_of ?? "없음"}) 기준입니다.
        </p>
      ) : recalcPending(summary) ? (
        <p className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          계산 중입니다. 아래 손익·보유는 마지막 계산({summary?.as_of ?? "없음"}) 기준이고, 방금 넣은 기록은 아직 반영되지 않았습니다.
        </p>
      ) : null}

      {/* 가장 자주 하는 일. 전에는 "[매매 기록] 탭에서 넣으라" 는 문장뿐이라 찾기 어려웠다 (2026-09-18) */}
      <button
        type="button"
        onClick={openTradeForm}
        className="mb-3 flex h-12 w-full items-center justify-center gap-1 rounded-xl bg-slate-900 text-base font-medium text-white sm:h-auto sm:w-auto sm:px-4 sm:py-1.5 sm:text-sm dark:bg-slate-100 dark:text-slate-900"
      >
        <span aria-hidden="true">＋</span> 매매 입력
      </button>

      {summary ? <Totals summary={summary} /> : null}
      {summary && (summary.flags ?? []).some((f) => !f.dismissed_at) ? (
        <p className="mb-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:bg-rose-950 dark:text-rose-200">
          매도 플래그 {(summary.flags ?? []).filter((f) => !f.dismissed_at).length}개가 걸려 있습니다. [보유] 에서 근거를 보고 확인하세요. 자동으로 팔지 않습니다.
        </p>
      ) : null}

      {/*
        폰: 다섯 칸이 한 줄에 들어가는 짧은 이름 + 건수. 전에는 옆으로 넘쳐 "복기" 가 잘렸다 (2026-09-18).
        넓은 화면: 전처럼 알약 모양, 긴 이름
      */}
      <div role="tablist" aria-label="포트폴리오" className="mb-3 grid grid-cols-5 gap-1 rounded-xl bg-slate-100 p-1 sm:flex sm:gap-2 sm:bg-transparent sm:p-0 dark:bg-slate-900 sm:dark:bg-transparent">
        {(
          [
            ["positions", "보유", "보유", summary?.positions.length ?? 0],
            ["trades", "기록", "매매 기록", trades.length],
            ["dividends", "배당", "배당", dividends.length],
            ["lots", "손익", "실현손익", summary?.lots.length ?? 0],
            ["review", "복기", "복기", review?.reviews.length ?? 0],
          ] as const
        ).map(([key, short, long, count]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`flex min-h-11 flex-col items-center justify-center rounded-lg text-xs sm:min-h-0 sm:flex-row sm:rounded-full sm:border sm:px-3 sm:py-1 ${
              tab === key
                ? "bg-white font-semibold text-slate-900 shadow-sm sm:border-slate-900 sm:bg-slate-900 sm:text-white dark:bg-slate-700 dark:text-white sm:dark:border-slate-100 sm:dark:bg-slate-100 sm:dark:text-slate-900"
                : "text-slate-600 sm:border-slate-300 dark:text-slate-300 sm:dark:border-slate-700"
            }`}
          >
            <span className="sm:hidden">{short}</span>
            <span className="hidden sm:inline">{long}</span>
            <span className="text-[10px] font-normal opacity-70 sm:ml-1 sm:text-xs sm:opacity-100">{count}</span>
          </button>
        ))}
      </div>

      {tab === "positions" && summary ? <Positions summary={summary} onDismissed={() => void load()} /> : null}
      {tab === "trades" ? <Trades trades={trades} onChanged={afterSave} onReload={() => void load()} /> : null}
      {tab === "dividends" ? <Dividends dividends={dividends} taxes={taxes} taxesUnread={taxesUnread} onChanged={afterSave} /> : null}
      {tab === "lots" && summary ? <Lots lots={summary.lots} /> : null}
      {tab === "review" ? <Review data={review} stale={recalcPending(summary)} /> : null}
    </div>
  );
}

function Totals({ summary }: { summary: Summary }) {
  const t = summary.totals;
  if (!t || !summary.has_trades) {
    return (
      <p className="mb-3 rounded-lg border border-slate-200 px-3 py-4 text-sm text-slate-500 dark:border-slate-800">
        아직 매매 기록이 없습니다. 위의 [＋ 매매 입력] 으로 첫 매수를 넣으면 보유와 손익이 계산됩니다.
      </p>
    );
  }
  const n = (k: string) => (typeof t[k] === "number" ? (t[k] as number) : null);
  const m = summary.metrics ?? {};
  return (
    <section className="mb-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
      {/* 평가액과 **같은 종목의** 원가를 보인다. 평가 못 한 종목은 따로 적는다 (docs/infra.md 25.330) */}
      <Stat
        label="평가액"
        value={won(n("market_value_krw"))}
        sub={
          `원가 ${won(n("valued_cost_krw") ?? n("cost_krw"))}`
          + ((n("unvalued_count") ?? 0) > 0 ? ` · 평가 못 한 ${n("unvalued_count")}종목(원가 ${won(n("unvalued_cost_krw"))}) 제외` : "")
        }
      />
      <Stat
        label="평가손익"
        value={signedWon(n("unrealized_pnl_krw"))}
        sub={`주가 ${signedWon(n("unrealized_price_pnl_krw"))} · 환율 ${signedWon(n("unrealized_fx_pnl_krw"))}`}
        tone={n("unrealized_pnl_krw")}
      />
      <Stat
        label="실현손익"
        value={signedWon(n("realized_pnl_krw"))}
        sub={`주가 ${signedWon(n("realized_price_pnl_krw"))} · 환율 ${signedWon(n("realized_fx_pnl_krw"))}`}
        tone={n("realized_pnl_krw")}
      />
      <Stat
        label="시간가중 수익률"
        value={pct(typeof m.twr_total_return === "number" ? m.twr_total_return : null)}
        sub={
          // 어느 기간의 수익률인지 적는다 — 첫 매매일부터 누적이다 (docs/infra.md 25.331)
          (typeof m.start_date === "string" ? `${m.start_date}부터 · ` : "")
          // 비용은 판 묶음의 수수료·세금만이다 — 아직 안 판 종목의 매수 수수료는 원가에 들어 있다 (25.399)
          + `배당 ${won(n("dividends_net_krw"))} · 실현분 비용 ${won(n("fees_taxes_krw"))}`
        }
      />
      <div className="col-span-2 text-xs text-slate-500 sm:col-span-4">
        계산 {summary.as_of ?? "-"}
        {/* 평가에 쓴 종가의 날짜 — 계산한 날과 다르다 (docs/infra.md 25.331) */}
        {t.price_date_min
          ? ` · 종가 ${String(t.price_date_min)}${t.price_date_max && t.price_date_max !== t.price_date_min ? `~${String(t.price_date_max)}` : ""}`
          : ""}
        {t.fx_usdkrw ? ` · 환율 ${Number(t.fx_usdkrw).toLocaleString("ko-KR")}원/달러 (${String(t.fx_date)})` : ""}
        {t.cost_estimated ? " · 수수료·세금 일부는 설정 비율로 추정" : ""}
        {/* 해외 양도세 연간 추정 — 배치가 낸 값을 그대로 보인다. 실현손익에는 섞지 않는다 (docs/infra.md 25.617) */}
        {Array.isArray(t.us_cgt_estimates)
          ? (t.us_cgt_estimates as { year: number; gains_krw: number; deduction_krw?: number; tax_krw: number | null }[])
              .filter((e) => e.gains_krw !== 0)
              .map((e) =>
                ` · ${e.year}년 해외 양도세 추정 ${e.tax_krw === null ? "(설정에 세율 없음)" : won(e.tax_krw)}`
                + ` (실현 ${signedWon(e.gains_krw)} − 공제 ${won(e.deduction_krw ?? null)}, 참고값)`,
              )
              .join("")
          : ""}
        {typeof m.cagr === "number" ? ` · CAGR ${pct(m.cagr)} · MDD ${pct(typeof m.mdd === "number" ? m.mdd : null)}` : ""}
      </div>
      {summary.upcoming.length > 0 ? (
        <p className="col-span-2 text-xs text-slate-500 sm:col-span-4">
          {/* 야후 예정일이 있으면 그것, 없으면 법정 기한 — 어느 쪽인지 종목마다 적는다 (docs/portfolio.md, 25.910) */}
          실적 일정: {summary.upcoming.slice(0, 5).map((u) => `${u.name} ${u.kind} D-${u.d_day} (${UPCOMING_BASIS[u.basis ?? "legal"] ?? "법정 기한"})`).join(" · ")}
        </p>
      ) : null}
      {summary.warnings.map((w) => (
        <p key={w} className="col-span-2 text-xs text-amber-700 dark:text-amber-300 sm:col-span-4">· {w}</p>
      ))}
    </section>
  );
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: number | null }) {
  // 반올림 전 값이 아니라 보이는 값으로 칠한다 (25.71·25.737, 감사)
  const color = pnlClass(tone);
  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-800">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`font-semibold ${color}`}>{value}</div>
      {sub ? <div className="text-[11px] text-slate-500">{sub}</div> : null}
    </div>
  );
}

function Positions({ summary, onDismissed }: { summary: Summary; onDismissed: () => void }) {
  const alloc = summary.allocation;
  const [err, setErr] = useState<string | null>(null);
  const dismiss = async (id: number) => {
    // **결과를 본다** (docs/infra.md 25.317). 예전에는 응답을 버려 실패해도 말이 없었고, 네트워크가 끊기면 예외로 끝났다
    const r = await readJson(`/api/sell-flags/${id}`, { method: "POST" });
    if (!r.ok) return setErr(`확인 처리 실패: ${r.error ?? "알 수 없음"}`);
    setErr(null);
    onDismissed();
  };
  if (summary.positions.length === 0) return <p className="py-4 text-sm text-slate-500">보유 종목이 없습니다.</p>;
  return (
    <div>
      {err ? <p className="mb-2 text-xs text-rose-600">{err}</p> : null}
      {alloc && Object.keys(alloc.by_sector).length > 0 ? (
        <p className="mb-2 text-xs text-slate-500">
          {/* **분모가 둘이다** (docs/infra.md 25.273). 구성은 보유 평가액 대비, 상한은 총 투자가능금액 대비(25.238).
              "업종 비중 반도체 60%" 옆에 "30% 상한" 이 있는데 경고가 없으면 틀린 것처럼 보였다 — 이름으로 가른다 */}
          업종 구성(보유 대비) {Object.entries(alloc.by_sector).slice(0, 6).map(([k, v]) => `${k} ${v.toFixed(1)}%`).join(" · ")}
          {/* 상한과 넘은 업종은 배치가 설정값으로 고른다 (docs/infra.md 25.217). 30 을 여기 박으면 설정과 갈라진다 */}
          {alloc.sectors_over_cap?.length && alloc.max_sector_pct != null
            ? ` — 총 투자가능금액의 ${alloc.max_sector_pct}% 상한을 넘는 업종: ${alloc.sectors_over_cap.join(", ")}`
            : ""}
        </p>
      ) : null}
      {lookthroughLines(alloc?.lookthrough).length ? (
        // ETF 가 무엇을 들고 있는지 펼쳐 본 노출 (docs/portfolio.md 8장, 25.1002). 계산은 배치가 했다
        <div className="mb-2 rounded-lg bg-slate-50 px-2 py-1.5 text-xs text-slate-600 dark:bg-slate-900 dark:text-slate-400">
          {lookthroughLines(alloc?.lookthrough).map((l) => <p key={l}>{l}</p>)}
        </div>
      ) : null}
      <div className="flex flex-col gap-2">
        {summary.positions.map((p) => (
          <article key={p.stock_id} className="rounded-xl border border-slate-200 px-3 py-2.5 text-sm dark:border-slate-800">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <Link href={`/stocks/${p.stock_id}`} className="font-semibold hover:underline">{p.name}</Link>
              <span className="text-xs text-slate-500">{p.ticker} · {p.sector ?? "업종 없음"} · {HORIZON[p.horizon ?? ""] ?? "-"}</span>
              <span className="ml-auto">{won(p.market_value_krw)} <span className="text-xs text-slate-500">({p.weight_pct?.toFixed(1) ?? "-"}%)</span></span>
            </div>
            {(summary.flags ?? []).filter((f) => f.stock_id === p.stock_id).map((f) => (
              <div key={f.id} className={`mt-1 rounded px-2 py-1 text-xs ${FLAG_STYLE[f.level] ?? ""} ${f.dismissed_at ? "opacity-60" : ""}`}>
                <p className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-semibold">[{FLAG_MARK[f.level]}] {f.reason_code}</span>
                  <span>{f.rationale_text}</span>
                  <span className="opacity-70">({f.first_seen_date}부터)</span>
                  {f.dismissed_at ? <span className="ml-auto opacity-70">확인함</span> : (
                    <button type="button" className={`ml-auto underline ${TAP}`} onClick={() => void dismiss(f.id)}>확인</button>
                  )}
                </p>
                {/* 근거표 — 종목 상세와 같은 것 (25.262) */}
                <FlagCriteria raw={f.rationale_data ?? null} asOf={f.as_of_date} />
              </div>
            ))}
            <div className="mt-1 grid grid-cols-2 gap-x-3 text-xs text-slate-600 dark:text-slate-300 sm:grid-cols-4">
              <span>{qtyText(p.quantity)}주 · 평단 {money(p.avg_price, p.currency)}</span>
              <span>현재가 {money(p.close, p.currency)} ({p.price_date ?? "-"})</span>
              <span className={pnlClass(p.unrealized_pnl_krw)}>
                평가손익 {signedWon(p.unrealized_pnl_krw)}
                {pctSuffix(p.unrealized_pnl_krw, p.cost_krw)}
              </span>
              {p.currency !== "KRW" ? (
                <span>주가 {signedWon(p.unrealized_price_pnl_krw)} · 환율 {signedWon(p.unrealized_fx_pnl_krw)}</span>
              ) : (
                <span>첫 매수 {p.first_buy_date}</span>
              )}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function StockPicker({ onPick }: { onPick: (s: Stock) => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Stock[]>([]);
  useEffect(() => {
    if (q.trim().length < 1) {
      setFound([]);
      return;
    }
    const timer = setTimeout(async () => {
      const j = bodyOf<{ stocks?: Stock[] }>(await readJson(`/api/stocks/search?q=${encodeURIComponent(q.trim())}`));;
      setFound(j.stocks ?? []);
    }, 250);
    return () => clearTimeout(timer);
  }, [q]);
  return (
    <div className="relative">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="종목 이름·티커 (예: 삼성전자, AAPL)"
        className={`w-full ${inputCls}`}
      />
      {found.length > 0 ? (
        <ul className="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded border border-slate-200 bg-white text-sm shadow dark:border-slate-700 dark:bg-slate-900">
          {found.map((s) => (
            <li key={s.id}>
              <button
                type="button"
                className="min-h-11 w-full px-3 py-2 text-left hover:bg-slate-100 sm:min-h-0 sm:px-2 sm:py-1 dark:hover:bg-slate-800"
                onClick={() => {
                  onPick(s);
                  setQ("");
                  setFound([]);
                }}
              >
                {s.name} <span className="text-xs text-slate-500">{s.ticker} · {s.market}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-0.5 text-xs text-slate-600 dark:text-slate-300">
      {label}
      {children}
    </label>
  );
}

/** 입력칸: 폰에서 손가락 높이(44px). 넓은 화면은 전처럼 낮게 (docs/pwa.md 3.2) */
const inputCls = "h-11 rounded-lg border border-slate-300 px-2 text-sm sm:h-auto sm:rounded sm:py-1 dark:border-slate-700 dark:bg-slate-900";
/** 목록 줄 안의 작은 단추(근거·지우기·확인): 폰에서 누를 자리를 넓힌다 */
const TAP = "min-h-10 px-2 sm:min-h-0 sm:px-0";
/** 저장 단추: 폰에서 폭 전체 */
const SAVE = "mt-3 h-12 w-full rounded-lg bg-slate-900 text-base text-white disabled:opacity-50 sm:h-auto sm:w-auto sm:rounded sm:px-3 sm:py-1.5 sm:text-sm dark:bg-slate-100 dark:text-slate-900";

/** 매매·배당 저장/삭제 응답 (docs/infra.md 25.281) */
type Saved = { recalc?: { dispatched: boolean; reason?: string }; warnings?: string[] };

/**
 * 매매 한 건 입력 (docs/infra.md 25.894). 포트폴리오의 "매매 기록 추가" 와 종목 상세·추천·적립의 "매매 입력" 이 **같은 폼**을 쓴다 —
 * 저장 규칙(보이는 칸만 싣기 25.265, 두 번 누름 확인 25.785, 단위 실수 확인 25.789)이 한 곳에만 있게.
 * `fixedStock` 을 주면 종목을 고르지 않는다(그 화면의 종목). 값은 사용자가 넣은 그대로 저장한다(CLAUDE.md trades 규칙)
 */
export function TradeForm({
  fixedStock,
  defaultHorizon = "long",
  defaultSide = "buy",
  onSaved,
  onReload,
}: {
  fixedStock?: Stock;
  defaultHorizon?: "short" | "mid" | "long";
  /** 처음 고른 매수·매도 — 리포트 딥링크(25.944). 없으면 매수 */
  defaultSide?: "buy" | "sell";
  onSaved: (r?: { dispatched: boolean; reason?: string }) => void;
  onReload?: () => void;
}) {
  const [stock, setStock] = useState<Stock | null>(fixedStock ?? null);
  const [side, setSide] = useState<"buy" | "sell">(defaultSide);
  const [date, setDate] = useState("");
  const [price, setPrice] = useState("");
  const [qty, setQty] = useState("");
  const [fx, setFx] = useState("");
  const [fee, setFee] = useState("");
  const [tax, setTax] = useState("");
  const [horizon, setHorizon] = useState<"short" | "mid" | "long">(defaultHorizon);
  const [memo, setMemo] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const num = readAmount; // 쉼표를 빼고 읽는다. 못 읽으면 NaN — 보내기 전에 막는다 (25.642)

  // `confirmed` 는 사용자가 확인 창에서 "그래도 저장" 한 종류들 — 서버가 묻는 것 하나에 답하고 모아서 다시 보낸다 (25.945)
  const submit = async (confirmed: ReadonlySet<ConfirmKind> = new Set()) => {
    if (!stock) return setErr("종목을 고르세요");
    // **보이는 칸만** 본다 — 숨은 환율·세금 칸의 옛 글이 저장을 막지 않게 (25.265 원칙, 25.645 교차검증)
    const 못읽음 = unreadableAmount({
      체결가: price, 수량: qty, 수수료: fee,
      ...(stock.currency !== "KRW" ? { 환율: fx } : {}),
      ...(side === "sell" ? { 세금: tax } : {}),
    });
    if (못읽음) return setErr(못읽음);
    setBusy(true);
    setErr(null);
    const r = await readJson("/api/trades", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // 보이는 칸만 싣는다 (docs/infra.md 25.265)
      body: JSON.stringify(tradeBody({
        stockId: stock.id, currency: stock.currency, side, date, price: num(price), quantity: num(qty),
        fx: num(fx), fee: num(fee), tax: num(tax), horizon, memo, confirmed,
      })),
    });
    const j = bodyOf<Saved & Record<string, unknown>>(r);
    setBusy(false);
    // 서버가 "그래도 저장할까요?" 를 묻는다 — 같은 매매 두 번(25.785), 체결가가 종가와 100배(25.789). 확인 루프는 하나다 (25.945)
    const 물음 = r.ok ? null : confirmNeeded(j, confirmed);
    if (물음) {
      if (confirm(confirmPrompt(물음, j.errors))) return submit(new Set([...confirmed, 물음]));
      // 중복을 취소하면 **목록만 다시 읽는다** (25.789·25.790, 교차검증) — 첫 저장이 실패처럼 보였을 때는 목록이 옛것이라 "목록을 확인"
      // 할 수 없었다. `onChanged` 는 "저장했습니다" 띠까지 띄워 방금 취소한 요청이 저장된 것으로 읽혔다
      if (물음 === "duplicate") onReload?.();
      return setErr(j.errors?.join(", ") ?? CONFIRM_CANCEL[물음]);
    }
    if (!r.ok) return setErr(j.errors?.join(", ") ?? "저장 실패");
    setPrice(""); setQty(""); setFx(""); setFee(""); setTax(""); setMemo("");
    // 저장은 됐지만 말할 것이 있으면(환율 범위 밖 등) 그 자리에서 (docs/infra.md 25.296)
    if (j.warnings?.length) setErr(j.warnings.join(" "));
    onSaved(j.recalc);
  };

  return (
    <section id={fixedStock ? undefined : "trade-form"} className="mb-4 scroll-mt-16 rounded-xl border border-slate-200 p-3 dark:border-slate-800">
      <h3 className="mb-2 text-sm font-medium">매매 기록 추가</h3>
      <div className="mb-2">{stock ? (
        <p className="text-sm">{stock.name} <span className="text-xs text-slate-500">{stock.ticker} · {stock.currency}</span>{" "}
          {fixedStock ? null : <button type="button" className="text-xs text-slate-500 underline" onClick={() => setStock(null)}>바꾸기</button>}</p>
      ) : <StockPicker onPick={setStock} />}</div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Field label="구분">
          <select value={side} onChange={(e) => setSide(e.target.value as "buy" | "sell")} className={inputCls}>
            <option value="buy">매수</option>
            <option value="sell">매도</option>
          </select>
        </Field>
        <Field label="체결일"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} className={inputCls} /></Field>
        <Field label={`체결가 (${stock?.currency ?? "통화"})`}><input type="text" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} className={inputCls} /></Field>
        <Field label="수량"><input type="text" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} className={inputCls} /></Field>
        {stock && stock.currency !== "KRW" ? (
          <Field label="환율 (원/달러, 비우면 그날 이하 최근 종가)"><input type="text" inputMode="decimal" value={fx} onChange={(e) => setFx(e.target.value)} className={inputCls} /></Field>
        ) : null}
        <Field label={`수수료 (${stock?.currency ?? "종목 통화"} · 비우면 설정 비율)`}><input type="text" inputMode="decimal" value={fee} onChange={(e) => setFee(e.target.value)} className={inputCls} /></Field>
        {side === "sell" ? <Field label={!stock ? "세금 (비우면 설정 비율)" : stock.currency === "KRW" && stock.asset_type === "etf" ? "세금 (KRW · ETF 는 증권거래세 없음 · 비우면 0)" : stock.currency === "KRW" ? "세금 (KRW · 비우면 설정 비율)" : `세금 (${stock.currency} · 매매세 없음 · 비우면 0)`}><input type="text" inputMode="decimal" value={tax} onChange={(e) => setTax(e.target.value)} className={inputCls} /></Field> : null}
        <Field label="투자 기간">
          <select value={horizon} onChange={(e) => setHorizon(e.target.value as "short" | "mid" | "long")} className={inputCls}>
            <option value="short">단기</option><option value="mid">중기</option><option value="long">장기</option>
          </select>
        </Field>
        <Field label="메모"><input value={memo} onChange={(e) => setMemo(e.target.value)} className={inputCls} /></Field>
      </div>
      {err ? <p className="mt-2 text-xs text-rose-600">{err}</p> : null}
      <button type="button" disabled={busy} onClick={() => void submit()} className={SAVE}>
        {busy ? "저장 중…" : "저장"}
      </button>
      <p className="mt-2 text-[11px] text-slate-500">매수를 저장하면 그날의 점수·신호를 함께 얼려 둡니다(복기용). 기록을 고치려면 지우고 다시 넣습니다.</p>
    </section>
  );
}

function Trades({ trades, onChanged, onReload }: { trades: Trade[]; onChanged: (r?: { dispatched: boolean; reason?: string }) => void; onReload: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  const remove = async (id: number) => {
    if (!confirm("이 기록을 지울까요? 손익은 다시 계산됩니다.")) return;
    const r = await readJson(`/api/trades/${id}`, { method: "DELETE" });
    const j = bodyOf<Saved>(r);
    if (!r.ok) return setErr(j.errors?.join(", ") ?? "삭제 실패");
    // **지운 뒤 남은 매도가 보유를 넘기면 그 자리에서 말한다** (docs/infra.md 25.150).
    // 배치의 같은 경고는 다음 재계산이 끝나야 보인다 — 그때까지 모르고 지나간다
    if (j.warnings?.length) setErr(j.warnings.join(" "));
    onChanged(j.recalc);
  };

  return (
    <div>
      <TradeForm onSaved={onChanged} onReload={onReload} />
      {err ? <p className="mb-2 text-xs text-rose-600">{err}</p> : null}

      {trades.length === 0 ? <p className="text-sm text-slate-500">기록이 없습니다.</p> : (
        <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 text-sm dark:divide-slate-800 dark:border-slate-800">
          {trades.map((t) => (
            <li key={t.id} className="px-3 py-2">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className={`text-xs font-medium ${t.side === "buy" ? "text-rose-600" : "text-blue-600"}`}>{t.side === "buy" ? "매수" : "매도"}</span>
                <span className="font-medium">{t.name}</span>
                <span className="text-xs text-slate-500">{t.trade_date} · {money(t.price, t.currency)} × {qtyText(t.quantity)}</span>
                {t.currency !== "KRW" ? <span className="text-xs text-slate-500">환율 {t.fx_rate.toLocaleString("ko-KR")} ({FX_SOURCE[t.fx_rate_source] ?? t.fx_rate_source})</span> : null}
                <span className="ml-auto flex gap-2 text-xs">
                  {t.side === "buy" ? <button type="button" className={`text-slate-500 underline ${TAP}`} onClick={() => setOpen(open === t.id ? null : t.id)}>근거</button> : null}
                  <button type="button" className={`text-slate-500 underline ${TAP}`} onClick={() => remove(t.id)}>지우기</button>
                </span>
              </div>
              {open === t.id ? (
                <p className="mt-1 text-xs text-slate-500">
                  {t.snapshot_as_of
                    ? `매수 당시(${t.snapshot_as_of}) 종합 점수 ${t.score_at_trade ?? "-"} · 신호 ${t.signal_type_at_trade ?? "없음"} · 팩터 ${t.factor_scores_at_trade ?? "-"}`
                    : "매수 당시 저장된 점수가 없습니다(그날 점수 계산 전이거나 유니버스 밖)"}
                </p>
              ) : null}
              {t.memo ? <p className="mt-0.5 text-xs text-slate-500">{t.memo}</p> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Dividends({ dividends, taxes, taxesUnread, onChanged }: { dividends: Dividend[]; taxes: DividendTaxes | null; taxesUnread: boolean; onChanged: (r?: { dispatched: boolean; reason?: string }) => void }) {
  const [stock, setStock] = useState<Stock | null>(null);
  const [date, setDate] = useState("");
  const [gross, setGross] = useState("");
  const [tax, setTax] = useState("");
  const [fx, setFx] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const num = readAmount; // 쉼표를 빼고 읽는다. 못 읽으면 NaN — 보내기 전에 막는다 (25.642)

  // **세금 칸을 설정 비율로 채운다** (docs/infra.md 25.127).
  //
  // 매매 폼은 "세금 (비우면 설정 비율)" 이라고 적어 두고 실제로 그렇게 도는데, 배당 폼은
  // 아무 말 없이 **비우면 0** 으로 저장했다 — 세전 금액이 그대로 실수령이 되어 총손익과
  // TWR 지수에 최대 15.4% 부풀려 들어간다.
  //
  // **추정해서 저장하지 않고 칸을 채운다.** `dividend_receipts` 는 사용자 입력 원본이라
  // 시스템이 고치지 않는다(migrations/0017). 저장되는 값은 언제나 사용자가 본 그 값이다.
  const 세율 = withholdingRate(stock?.currency ?? "KRW", taxes);
  const 손댔나 = useRef(false);
  const 세전바뀜 = (v: string) => {
    setGross(v);
    if (손댔나.current) return;
    const 채울값 = estimateWithholding(num(v), 세율, stock?.currency ?? "KRW");
    setTax(채울값 === null ? "" : String(채울값));
  };
  // **종목을 바꾸면 세율도 바뀐다** (docs/infra.md 25.405). 예전에는 세전 칸이 바뀔 때만 채워서, 국내 종목(15.4%)으로
  // 채운 뒤 미국 종목으로 바꾸면 그 값이 그대로 저장됐다. 사용자가 세금을 직접 고쳤으면 건드리지 않는다
  useEffect(() => {
    if (손댔나.current) return;
    const 채울값 = estimateWithholding(num(gross), 세율, stock?.currency ?? "KRW");
    setTax(채울값 === null ? "" : String(채울값));
    // 세전 칸 입력은 세전바뀜 이 맡는다 — 여기서는 종목·세율이 바뀔 때만 다시 채운다
  }, [stock?.currency, 세율]);

  const submit = async (confirmed: ReadonlySet<ConfirmKind> = new Set()): Promise<void> => {
    if (!stock) return setErr("종목을 고르세요");
    const 못읽음 = unreadableAmount({ "세전 금액": gross, 세금: tax, ...(stock.currency !== "KRW" ? { 환율: fx } : {}) });
    if (못읽음) return setErr(못읽음);
    setBusy(true);
    setErr(null);
    const r = await readJson("/api/dividends", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(dividendBody({ stockId: stock.id, currency: stock.currency, date, gross: num(gross), tax: num(tax), fx: num(fx), confirmed })),
    });
    const j = bodyOf<Saved & Record<string, unknown>>(r);
    setBusy(false);
    // 서버가 "그래도 저장할까요?" 를 묻는다 — 방금 같은 배당(25.829), 원금보다 큰 배당(25.934). 매매와 같은 루프 하나 (25.945).
    // 취소면 저장하지 않았다고만 말한다 — `onChanged` 는 "저장했습니다" 띠를 띄운다(25.790)
    const 물음 = r.ok ? null : confirmNeeded(j, confirmed);
    if (물음) {
      if (confirm(confirmPrompt(물음, j.errors))) return submit(new Set([...confirmed, 물음]));
      return setErr(j.errors?.join(", ") ?? CONFIRM_CANCEL[물음]);
    }
    if (!r.ok) return setErr(j.errors?.join(", ") ?? "저장 실패");
    setGross(""); setTax(""); setFx(""); 손댔나.current = false;
    if (j.warnings?.length) setErr(j.warnings.join(" "));
    onChanged(j.recalc);
  };

  const remove = async (id: number) => {
    if (!confirm("이 배당 기록을 지울까요?")) return;
    const r = await readJson(`/api/dividends/${id}`, { method: "DELETE" });
    const j = bodyOf<Saved>(r);
    if (!r.ok) return setErr(j.errors?.join(", ") ?? "삭제 실패");
    onChanged(j.recalc);
  };

  return (
    <div>
      <section className="mb-4 rounded-xl border border-slate-200 p-3 dark:border-slate-800">
        <h3 className="mb-2 text-sm font-medium">배당 수령 추가</h3>
        <div className="mb-2">{stock ? (
          <p className="text-sm">{stock.name} <span className="text-xs text-slate-500">{stock.ticker}</span>{" "}
            <button type="button" className="text-xs text-slate-500 underline" onClick={() => setStock(null)}>바꾸기</button></p>
        ) : <StockPicker onPick={setStock} />}</div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Field label="지급일"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} className={inputCls} /></Field>
          <Field label={`세전 금액 (${stock?.currency ?? "통화"})`}><input type="text" inputMode="decimal" value={gross} onChange={(e) => 세전바뀜(e.target.value)} className={inputCls} /></Field>
          <Field label={세율 === null ? "원천징수 세금" : `원천징수 세금 (설정 ${세율}%)`}><input type="text" inputMode="decimal" value={tax} onChange={(e) => { 손댔나.current = true; setTax(e.target.value); }} className={inputCls} /></Field>
          {stock && stock.currency !== "KRW" ? <Field label="환율 (비우면 그날 이하 최근 종가)"><input type="text" inputMode="decimal" value={fx} onChange={(e) => setFx(e.target.value)} className={inputCls} /></Field> : null}
        </div>
        {taxesUnread ? (
          <p className="mt-2 text-xs font-medium text-amber-700 dark:text-amber-300">
            설정을 읽지 못해 세율을 채우지 못했습니다 — 세율이 없는 것이 아닙니다. 세금을 직접 넣거나 잠시 뒤 새로고침하세요.
          </p>
        ) : 세율 === null ? (
          <p className="mt-2 text-xs text-slate-500">
            설정에 배당소득세율이 없거나 범위(0~50%)를 벗어나 세금을 채우지 못했습니다. 비워 두면 <strong>0 으로 기록</strong>됩니다 —{" "}
            <Link href="/settings" className="underline">설정</Link>에서 세율을 넣으면 다음부터 채워 드립니다.
          </p>
        ) : null}
        {err ? <p className="mt-2 text-xs text-rose-600">{err}</p> : null}
        <button type="button" disabled={busy} onClick={() => void submit()} className={SAVE}>
          {busy ? "저장 중…" : "저장"}
        </button>
      </section>
      {dividends.length === 0 ? <p className="text-sm text-slate-500">기록이 없습니다.</p> : (
        <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 text-sm dark:divide-slate-800 dark:border-slate-800">
          {dividends.map((d) => (
            <li key={d.id} className="flex flex-wrap items-baseline gap-x-2 px-3 py-2">
              <span className="font-medium">{d.name}</span>
              <span className="text-xs text-slate-500">{d.pay_date} · 세전 {money(d.gross_amount, d.currency)} · 세후 {money(d.net_amount, d.currency)}</span>
              {d.currency !== "KRW" ? <span className="text-xs text-slate-500">환율 {d.fx_rate.toLocaleString("ko-KR")}</span> : null}
              <button type="button" className={`ml-auto text-xs text-slate-500 underline ${TAP}`} onClick={() => remove(d.id)}>지우기</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Lots({ lots }: { lots: Lot[] }) {
  if (lots.length === 0) return <p className="py-4 text-sm text-slate-500">아직 판 기록이 없습니다.</p>;
  return (
    <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 text-sm dark:divide-slate-800 dark:border-slate-800">
      {lots.map((l) => (
        <li key={l.id} className="px-3 py-2">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-medium">{l.name}</span>
            <span className="text-xs text-slate-500">{l.sell_date} · {qtyText(l.quantity)}주 · {money(l.buy_price, l.currency)} → {money(l.sell_price, l.currency)} · {l.holding_days}일</span>
            <span className={`ml-auto ${pnlClass(l.realized_pnl_krw)}`}>{signedWon(l.realized_pnl_krw)}</span>
          </div>
          <div className="text-xs text-slate-500">
            {l.currency !== "KRW" ? `주가 ${signedWon(l.price_pnl_krw)} · 환율 ${signedWon(l.fx_pnl_krw)} · ` : ""}
            비용 {money(l.fee_total + l.tax_total, l.currency)}{l.cost_estimated ? " (일부 추정)" : ""}
          </div>
        </li>
      ))}
    </ul>
  );
}

// ----------------------------------------------------------------------
// 복기 (docs/review.md)
// ----------------------------------------------------------------------

function Review({ data, stale }: { data: ReviewData | null; stale: boolean }) {
  if (!data) return <p className="py-4 text-sm text-slate-500">불러오는 중...</p>;
  return (
    <div>
      {data.notice ? (
        <p className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">{data.notice}</p>
      ) : null}
      {stale ? <p className="mb-2 text-xs text-slate-500">계산 중입니다. 방금 넣은 매도는 아직 복기에 없습니다.</p> : null}
      <ReviewStats stats={data.stats} />
      <ReviewList reviews={data.reviews} />
      <p className="mt-3 text-[11px] text-slate-500">
        단위는 매수 결정 하나(같은 매수에서 나온 체결 묶음을 합침). 목표·손절은 지금 설정값이라 매수 당시와 다를 수 있다.
        배당은 빼고 본다.
      </p>
    </div>
  );
}

function ReviewStats({ stats }: { stats: ReviewStat[] }) {
  if (stats.length === 0) return null;
  const r = (v: number | null) => pct(v);
  // 반올림으로 끝값을 만들지 않는다 — 199/200 이 "100%" 였다 (docs/infra.md 25.699·25.704). 성적표와 같은 함수
  const rate = (v: number | null) => (v === null ? "-" : rateText(v));
  return (
    <>
    {/* 폰: 집단마다 카드. 아홉 칸 표는 폰에서 옆으로 넘쳤다 (2026-09-18) */}
    <ul className="mb-3 flex flex-col gap-2 text-xs sm:hidden">
      {stats.map((s) => {
        const note = sampleNote(s);
        return (
          <li key={s.group_key} className={`rounded-xl border border-slate-200 px-3 py-2 dark:border-slate-800 ${note ? "text-slate-400 dark:text-slate-500" : ""}`}>
            <p className="font-medium">
              {s.group_kind === "signal" ? `신호 · ${s.label}` : s.label} <span className="font-normal text-slate-400">{s.n}건</span>
              {note ? <span className="ml-1 font-normal">({note})</span> : null}
            </p>
            <dl className="mt-1 grid grid-cols-3 gap-x-3 gap-y-0.5">
              {(
                [
                  ["승률", rate(s.win_rate)],
                  ["평균", r(s.avg_return_pct)],
                  ["중앙값", r(s.median_return_pct)],
                  ["보유일", s.avg_holding_days === null ? "-" : `${s.avg_holding_days.toFixed(0)}일`],
                  ["목표 도달", rate(s.target_rate)],
                  ["손절 이탈", rate(s.stop_rate)],
                ] as Array<[string, string]>
              ).map(([k, v]) => (
                <div key={k} className="flex justify-between gap-1">
                  <dt className="text-slate-400">{k}</dt>
                  <dd className="tabular-nums">{v}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-1">실현손익 {signedWon(s.total_pnl_krw)} <span className="text-slate-400">(평균은 원가가중)</span></p>
          </li>
        );
      })}
    </ul>
    <div className="mb-3 hidden overflow-x-auto rounded-xl border border-slate-200 text-xs sm:block dark:border-slate-800">
      <table className="w-full">
        <thead className="bg-slate-50 text-left text-slate-500 dark:bg-slate-900">
          <tr>
            <th className="px-3 py-1.5 font-medium">집단</th>
            <th className="px-3 py-1.5 font-medium">건</th>
            <th className="px-3 py-1.5 font-medium">승률</th>
            <th className="px-3 py-1.5 font-medium">평균(원가가중)</th>
            <th className="px-3 py-1.5 font-medium">중앙값</th>
            <th className="px-3 py-1.5 font-medium">보유일</th>
            <th className="px-3 py-1.5 font-medium">목표 도달</th>
            <th className="px-3 py-1.5 font-medium">손절 이탈</th>
            <th className="px-3 py-1.5 font-medium">실현손익</th>
          </tr>
        </thead>
        <tbody>
          {stats.map((s) => {
            const note = sampleNote(s);
            return (
              <tr
                key={s.group_key}
                className={`border-t border-slate-100 dark:border-slate-800 ${note ? "text-slate-400 dark:text-slate-500" : ""}`}
                title={note ?? undefined}
              >
                <td className="whitespace-nowrap px-3 py-1.5 font-medium">
                  {s.group_kind === "signal" ? `신호 · ${s.label}` : s.label}
                  {note ? <span className="ml-1 font-normal">({note})</span> : null}
                </td>
                <td className="px-3 py-1.5">{s.n}</td>
                <td className="px-3 py-1.5">{rate(s.win_rate)}</td>
                <td className="px-3 py-1.5">{r(s.avg_return_pct)}</td>
                <td className="px-3 py-1.5">{r(s.median_return_pct)}</td>
                <td className="px-3 py-1.5">{s.avg_holding_days === null ? "-" : `${s.avg_holding_days.toFixed(0)}일`}</td>
                <td className="px-3 py-1.5">{rate(s.target_rate)}</td>
                <td className="px-3 py-1.5">{rate(s.stop_rate)}</td>
                <td className="whitespace-nowrap px-3 py-1.5">{signedWon(s.total_pnl_krw)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
    </>
  );
}

function ReviewList({ reviews }: { reviews: ReviewRow[] }) {
  if (reviews.length === 0) return <p className="py-4 text-sm text-slate-500">아직 청산한 매수가 없습니다. 팔고 나면 여기서 복기합니다.</p>;
  return (
    <ul className="flex flex-col gap-2">
      {reviews.map((r) => (
        <li key={r.buy_trade_id} className="rounded-xl border border-slate-200 px-3 py-2.5 text-sm dark:border-slate-800">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <Link href={`/stocks/${r.stock_id}`} className="font-semibold hover:underline">{r.name}</Link>
            <span className="text-xs text-slate-500">{r.ticker} · {r.buy_date} → {r.last_sell_date}</span>
            <span className={`rounded px-1.5 py-0.5 text-[11px] ${OUTCOME_TONE[r.outcome] ?? ""}`}>{OUTCOME_LABEL[r.outcome] ?? r.outcome}</span>
            <span className={`ml-auto ${pctClass(r.return_pct)}`}>
              {/* 수익률은 종목 통화 기준이다. 해외는 그렇다고 적고 원화 손익을 주가·환으로 나눈다 (25.241) */}
              {pct(r.return_pct)}{r.currency === "KRW" ? "" : ` (${r.currency} 주가)`}{" "}
              <span className="text-xs text-slate-500">{reviewPnlText(r, signedWon)}</span>
            </span>
          </div>
          <p className="mt-1 text-xs text-slate-700 dark:text-slate-300">{r.verdict_text}</p>
          <details className="mt-1 text-xs">
            <summary className="cursor-pointer select-none text-slate-500">근거 보기</summary>
            <div className="mt-1 grid gap-2 sm:grid-cols-2">
              <ReviewFacts
                title={`매수 당시${r.snapshot_as_of ? ` (기준일 ${r.snapshot_as_of})` : ""}`}
                rows={
                  r.has_snapshot
                    ? [
                        ["투자 기간", HORIZON[r.horizon ?? ""] ?? "없음"],
                        ["신호", r.signal_type_at_trade ?? "신호 없이 매수"],
                        ["종합 점수", r.score_at_trade === null ? "-" : r.score_at_trade.toFixed(0)],
                        ["센티먼트", r.sentiment_at_trade === null ? "-" : signedInt(r.sentiment_at_trade)],
                        ...factorEntries(r.factor_scores_at_trade).map(([k, v]): [string, string] => [`팩터 ${k}`, v.toFixed(0)]),
                      ]
                    : [["투자 기간", HORIZON[r.horizon ?? ""] ?? "없음"], ["근거", "저장된 스냅샷 없음 (점수가 없던 날이거나 옛 기록)"]]
                }
              />
              <ReviewFacts
                title="결과"
                rows={[
                  ["수량", `${qtyText(r.quantity_sold)}주${r.partial ? ` / 매수 ${qtyText(r.quantity_bought)}주 (일부 매도)` : ""}`],
                  ["원가 → 실수령", `${money(r.cost, r.currency)} → ${money(r.proceeds, r.currency)}`],
                  ["보유일", `${r.holding_days.toFixed(0)}일`],
                  ["실현손익", r.currency === "KRW" ? signedWon(r.realized_pnl_krw) : `${signedWon(r.realized_pnl_krw)} (주가 ${signedWon(r.price_pnl_krw)} · 환율 ${signedWon(r.fx_pnl_krw)})`],
                  ["목표 / 손절 (마지막 계산 때 설정)", r.target_pct === null || r.stop_pct === null ? "기간이 없어 판정 안 함" : `${r.target_pct > 0 ? "+" : ""}${r.target_pct}% / ${r.stop_pct}%`],
                  ["판정", OUTCOME_LABEL[r.outcome] ?? r.outcome],
                ]}
              />
            </div>
          </details>
        </li>
      ))}
    </ul>
  );
}

function ReviewFacts({ title, rows }: { title: string; rows: Array<[string, string]> }) {
  return (
    <table className="w-full rounded-lg border border-slate-200 dark:border-slate-800">
      <caption className="px-2 py-1 text-left font-medium text-slate-600 dark:text-slate-300">{title}</caption>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k} className="border-t border-slate-100 dark:border-slate-800">
            <td className="whitespace-nowrap px-2 py-1 text-slate-500">{k}</td>
            <td className="px-2 py-1">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
