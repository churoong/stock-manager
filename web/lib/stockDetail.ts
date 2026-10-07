/**
 * 종목 상세 조회 (docs/stock_detail.md, Step 10).
 *
 * 섹션마다 따로 읽는다. 한 섹션이 비거나 실패해도 나머지는 보여야 한다(완료 기준).
 * 섹션은 늘 `{ as_of, empty, reason }` 을 함께 돌려준다. 화면은 empty 면 "데이터 없음" 과 reason 을,
 * 아니면 as_of 를 기준 시각으로 적는다.
 *
 * 웹은 계산하지 않는다(CLAUDE.md). 배치가 저장한 값을 고르고 묶기만 한다.
 * 고르기(연도마다 연결 우선, 창마다 최신 계산판)는 계산이 아니라 조회 규칙이라 여기 둔다.
 */

import { ifMissingTable, ifMissingTableThen } from "@/lib/db";
import { LAST_CALC_DATE, LAST_CALC_DATE_FALLBACK, LAST_SIGNAL_SUCCESS, LATEST_SIGNAL_RUN, type SignalRunRow, signalRunNote } from "@/lib/recommend";
import { LAST_EDIT, LAST_RECALC_RUN, TRADES_VERSION, recalcStuckNote } from "@/lib/portfolio";
import { localDate } from "@/lib/market";
import { unmatchedNameNote } from "@/lib/newsKr";
import { z } from "zod";

export type Row = Record<string, unknown>;
/** 질의 실행기. 라우트는 Turso, 테스트는 node:sqlite 를 넘긴다 */
export type Exec = (sql: string, args?: Array<string | number | null>) => Promise<Row[]>;

export const SECTIONS = [
  "prices", "financials", "valuation", "metrics", "news", "events", "signals", "dividends", "quarterly", "opinions",
] as const;
export type Section = (typeof SECTIONS)[number];

export interface SectionResult {
  as_of: string | null;
  empty: boolean;
  /** empty 일 때 왜 비었는지. 사용자가 무엇을 기다리면 되는지 알 수 있게 */
  reason: string | null;
  [key: string]: unknown;
}

export const RANGES = { "3M": 92, "1Y": 366, "3Y": 1096, "5Y": 1827 } as const;
export const rangeSchema = z.enum(["3M", "1Y", "3Y", "5Y"]).default("1Y");

export function parseStockId(raw: string): number | null {
  const id = Number(raw);
  return Number.isInteger(id) && id > 0 ? id : null;
}

/** 기간 시작일. 오늘이 아니라 마지막 거래일에서 거슬러 센다(휴장이 길어도 차트가 비지 않게) */
export function rangeStart(range: keyof typeof RANGES, lastDate: string): string {
  const d = new Date(`${lastDate}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - RANGES[range]);
  return d.toISOString().slice(0, 10);
}

export function parseJson<T>(raw: unknown, fallback: T): T {
  if (typeof raw !== "string" || !raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function empty(reason: string, extra: Row = {}): SectionResult {
  return { as_of: null, empty: true, reason, ...extra };
}

// ---------------------------------------------------------------------------
// 질의
// ---------------------------------------------------------------------------

/** 증권사 의견 최근 15건과 그 증권사의 성적 (docs/brokers.md, 25.995) — KIS 투자의견 `kr_opinions` × 성적표 `broker_stats` */
export const OPINIONS = `SELECT o.date, o.broker, o.opinion, o.target_price, o.source, o.fetched_at FROM kr_opinions o
WHERE o.stock_id = ? ORDER BY o.date DESC, o.broker LIMIT 15`;
export const BROKER_STATS_FOR = `SELECT b.* FROM json_each(?) j JOIN broker_stats b ON b.broker = j.value`;

export const MASTER = `SELECT id, ticker, market, country, COALESCE(name_ko, name_en, ticker) AS name, name_en, name_ko, sector,
  sector_source, currency, listed_shares, status, fetched_at, asset_type
FROM stocks WHERE id = ?`;

export const LATEST_SCORE = `SELECT as_of_date, total_score, factor_scores, sentiment_score, sentiment_weight_used, weights_json,
  rank_in_market, rank_in_sector, skip_reason, calc_version
FROM scores WHERE stock_id = ? ORDER BY as_of_date DESC, calc_version DESC LIMIT 1`;

export const LATEST_FACTORS = `SELECT factor, raw_json, zscore, score, peer_group, peer_size, missing_fields, as_of_date, calc_version
FROM factors WHERE stock_id = ? AND as_of_date = (SELECT MAX(as_of_date) FROM factors WHERE stock_id = ?)
ORDER BY factor, calc_version DESC`;

/** 보유 표를 만든 매매 기록 지문 (portfolio_summary.trades_version). `TRADES_VERSION` 과 다르면 재계산 전이다 */
export const POSITION_SUMMARY_VERSION = `SELECT trades_version, created_at FROM portfolio_summary WHERE id = 1`;

/**
 * 보유 칸이 매매 기록보다 늦은가 (docs/infra.md 25.806, 종목 상세 감사). 매매를 넣고 재계산(1~2분)이 끝나기 전에는 positions 가
 * 옛 값이라, 방금 판 종목이 "내 보유 10주" 로, 방금 산 종목은 보유 칸 없이 보였다. 포트폴리오 화면(`/api/portfolio` 의 stale)과 같은 잣대.
 * 둘 중 하나라도 못 읽으면 null(모름) — 늦었다고도 아니라고도 하지 않는다. 요약 행이 아직 없으면(첫 매매 전후) 기록이 있을 때만 늦은 것이다
 */
export function positionStale(summary: Row[] | null, current: Row[] | null): boolean | null {
  if (summary === null || current === null) return null;
  const 지금 = String(current[0]?.version ?? "");
  // 기록이 하나도 없는지는 포트폴리오 화면의 has_trades 와 같은 모양으로 본다(지문 문자열을 통째로 박지 않는다, 25.809)
  if (!summary.length) return !(지금.startsWith("t0:") && 지금.includes("|d0:"));
  return String(summary[0].trades_version ?? "") !== 지금;
}

/** 뉴스 칸이 빌 때 까닭을 가르는 읽기 — 이름·나라와 수집 대상 여부(뉴스 크론과 같은 후보: news_targets ∪ 보유 ∪ 관심). 인자 [id×4] (25.835) */
export const NEWS_TARGET_STATE = `SELECT s.country, s.name_ko,
  CASE WHEN (EXISTS (SELECT 1 FROM news_targets WHERE stock_id = ?) OR EXISTS (SELECT 1 FROM positions WHERE stock_id = ?)
    OR EXISTS (SELECT 1 FROM watchlist WHERE stock_id = ?))
    -- 크론의 조건까지 — 국내는 활성·한글 이름, 미국은 활성·야후 심볼 (25.838, 교차검증)
    AND s.status = 'active' AND ((s.country = 'KR' AND s.name_ko IS NOT NULL) OR (s.country <> 'KR' AND s.yahoo_symbol IS NOT NULL))
  THEN 1 ELSE 0 END AS targeted
FROM stocks s WHERE s.id = ?`;

export const POSITION = `SELECT quantity, currency, avg_price, cost, first_buy_date, horizon, price_date, close, market_value,
  unrealized_pnl, unrealized_pnl_krw, unrealized_price_pnl_krw, unrealized_fx_pnl_krw, weight_pct, updated_at
FROM positions WHERE stock_id = ?`;

/**
 * 매도 플래그. **`rationale_data` 를 꼭 함께 읽는다** — 근거표다.
 *
 * 2026-09-21 까지 이 질의는 그 열을 빼고 읽었다. 배치는 `{criteria: [...]}` 를 꼬박꼬박
 * 저장하고 있었는데 화면은 요약 한 줄만 보여 줬다 — **CLAUDE.md 의 "근거표를 만들 수
 * 없는 추천은 표시하지 않는다" 가 깨진 자리**다 (docs/infra.md 25.94).
 */
export const STOCK_FLAGS = `SELECT id, level, reason_code, rationale_text, rationale_data, first_seen_date, dismissed_at, as_of_date
FROM sell_flags
WHERE stock_id = ? AND is_active = 1 AND as_of_date = (SELECT MAX(as_of_date) FROM sell_flags)
ORDER BY CASE level WHEN 'red' THEN 0 WHEN 'yellow' THEN 1 ELSE 2 END`;

export const WATCH = `SELECT id, target_buy_price, alert_enabled, memo FROM watchlist WHERE stock_id = ?`;

export const LAST_PRICE_DATE = `SELECT MAX(date) AS last FROM prices WHERE stock_id = ?`;

export const PRICES = `SELECT date, open, high, low, close, volume, currency, source, fetched_at
FROM prices WHERE stock_id = ? AND date >= ? ORDER BY date`;

/**
 * 사업보고서(10-K 도 11011 로 저장). **종목마다 가장 늦은 사업연도에 연결이 있으면 연결만, 없으면 별도만** (docs/infra.md 25.314·25.334·25.550).
 * 예전에는 연도마다 연결을 먼저 고르고 없으면 그해만 별도를 써 `2021* 2022* 2023 …` 처럼 기준이 섞였다 —
 * 점수·신호는 한 기준만 보는데 화면은 별도→연결 전환의 매출 점프를 성장처럼 보였다
 */
export const FINANCIALS = `SELECT fiscal_year, consolidated, report_date, receipt_no, accounting_standard, currency, unit,
  revenue, operating_income, pretax_income, net_income, total_assets, total_liabilities, total_equity, source, fetched_at
FROM financials WHERE stock_id = ? AND report_code = '11011'
  AND consolidated = (SELECT fb.consolidated FROM financials fb WHERE fb.stock_id = financials.stock_id AND fb.report_code = financials.report_code ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1)
ORDER BY fiscal_year DESC, consolidated DESC LIMIT 12`;

export const VALUATION = `SELECT as_of_date, metric, current_value, p20, p30, p50, p80, sample, band_rank, peer_percentile,
  peer_group, peer_size, price_date, equity_report_date, listed_shares, currency, skip_reason, calc_version, created_at,
  -- 밴드가 쓴 자본총계의 기준(연결 1·별도 0). 배치와 같은 규칙: 가장 늦은 사업연도에 연결이 있으면 연결 (docs/infra.md 25.334·25.550)
  -- 밴드 기준일까지 접수된 보고서로 고른다 — 배치(25.856)와 같다 (25.861)
  (SELECT fb.consolidated FROM financials fb WHERE fb.stock_id = valuation_bands.stock_id AND fb.report_code = '11011'
     AND fb.report_date <= valuation_bands.as_of_date ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1)
    AS equity_consolidated
FROM valuation_bands WHERE stock_id = ? ORDER BY as_of_date DESC, calc_version DESC LIMIT 1`;

export const METRICS = `SELECT window, as_of_date, cagr, mdd, mdd_peak_date, mdd_trough_date, mdd_recovery_days, volatility_ann,
  sharpe, sortino, beta, benchmark, risk_free_rate_used, data_points, calc_version, created_at
FROM performance_metrics
WHERE stock_id = ? AND as_of_date = (SELECT MAX(as_of_date) FROM performance_metrics WHERE stock_id = ?)
ORDER BY window, calc_version DESC`;

/** 한 기사에 채점이 여럿이면(방식 변경) 마지막 것 */
export const NEWS = `SELECT n.id, n.title, n.url, n.published_at, n.publisher, n.source, n.lang,
  (SELECT a.score FROM article_sentiments a WHERE a.news_id = n.id ORDER BY a.id DESC LIMIT 1) AS score,
  (SELECT a.method FROM article_sentiments a WHERE a.news_id = n.id ORDER BY a.id DESC LIMIT 1) AS method
FROM news n WHERE n.stock_id = ? ORDER BY n.published_at DESC LIMIT 30`;

export const SENTIMENT_TREND = `SELECT as_of_date, sentiment, article_count, positive_count, negative_count, negative_count_7d,
  delta_7d, delta_30d, method, created_at
FROM sentiment_scores WHERE stock_id = ? ORDER BY as_of_date DESC LIMIT 90`;

/**
 * 실적 일정. 두 출처가 섞인다 (docs/portfolio.md 5장).
 *   yfinance:calendar  야후 예정일. 날짜가 하나면 확정, 구간이면 추정
 *   estimate:filing_deadline  법정 제출 기한 추정 (야후가 날짜를 주지 않은 종목)
 */
export const EARNINGS = `SELECT event_type, scheduled_date, is_confirmed, note, source, fetched_at
FROM earnings_calendar WHERE stock_id = ? AND scheduled_date >= ? ORDER BY scheduled_date LIMIT 10`;

export const DISCLOSURES = `SELECT title, disclosed_at, url, report_name, is_material, source, fetched_at
FROM disclosures WHERE stock_id = ? ORDER BY disclosed_at DESC LIMIT 10`;

/**
 * 배당 이력 (docs/data-sources.md 1.1, 국내 DART 배당에 관한 사항).
 *
 * 같은 사업연도를 여러 보고서가 적으면(당기·전기·전전기) **가장 최근 보고서**의 값을 쓴다.
 * 정정되거나 확정된 값이 늦게 온 것이라 그쪽이 맞다.
 */
/**
 * 분기·반기 재무 (국내만. DART 는 11012 반기·11013 1분기·11014 3분기를 준다).
 *
 * **금액을 가공하지 않는다.** 보고서에 적힌 값 그대로다. 손익은 **그 분기 3개월 값**이다 — 반기·3분기
 * 보고서도 DART 가 thstrm_amount 에 3개월 값을 준다(누적은 thstrm_add_amount). 2026-09-18 삼성전자·SK하이닉스
 * 2025 전 분기로 확인했다(docs/data-sources.md 1.3). 그래서 열 이름을 보고서 이름이 아니라 분기로 쓴다.
 * 미국(SEC)은 연간(10-K)만 수집한다 — 분기 행이 없어 이 섹션이 빈다.
 */
export const QUARTERLY = `SELECT fiscal_year, report_code, period_type, report_date, currency, consolidated,
  revenue, operating_income, net_income, total_equity, source, fetched_at
FROM financials WHERE stock_id = ? AND report_code <> '11011'
  -- 연결이 있으면 연결, 없으면 별도 (docs/infra.md 25.314). **분기 보고서 전체에서 한 기준**을 고른다 — 보고서 코드마다
  -- 따로 고르면 1분기 별도 → 반기 연결 → 3분기 별도가 한 표에 섞여 매출 점프가 성장처럼 보였다 (25.550, DART 감사)
  AND consolidated = (SELECT fb.consolidated FROM financials fb WHERE fb.stock_id = financials.stock_id AND fb.report_code <> '11011'
    ORDER BY fb.fiscal_year DESC, CASE fb.report_code WHEN '11013' THEN 1 WHEN '11012' THEN 2 WHEN '11014' THEN 3 ELSE 4 END DESC,
      fb.consolidated DESC LIMIT 1)
-- 보고서 코드 순서는 시간 순서가 아니다: 11012 반기 · 11013 1분기 · 11014 3분기.
-- 코드로 정렬하면 1분기가 반기 뒤로 간다. 분기 번호로 세운다
ORDER BY fiscal_year DESC, CASE report_code WHEN '11013' THEN 1 WHEN '11012' THEN 2 WHEN '11014' THEN 3 ELSE 4 END DESC
LIMIT 8`;

/**
 * 분기·반기 표의 열 이름. 금액이 **어느 3개월**인지를 적는다.
 * "반기" 라고 적으면 1~6월 누적으로 읽힌다 — 실제로는 4~6월 값이다 (docs/data-sources.md 1.3).
 */
export const QUARTER_LABEL: Record<string, string> = {
  "11013": "1분기(1~3월)",
  "11012": "2분기(4~6월)",
  "11014": "3분기(7~9월)",
};

/** 보고서 코드 → 사람이 읽는 이름. 정의처는 batch/sources/dart.REPORT_CODES */
export const REPORT_LABEL: Record<string, string> = {
  "11011": "사업",
  "11012": "반기",
  "11013": "1분기",
  "11014": "3분기",
};

export const DIVIDENDS = `SELECT d.fiscal_year, d.report_year, d.as_of_date, d.cash_dividend_total, d.dps_common,
  d.payout_ratio, d.payout_basis, d.yield_common, d.source, d.fetched_at
FROM stock_dividends d
WHERE d.stock_id = ? AND d.report_year = (
  SELECT MAX(x.report_year) FROM stock_dividends x WHERE x.stock_id = d.stock_id AND x.fiscal_year = d.fiscal_year
)
ORDER BY d.fiscal_year DESC LIMIT 10`;

/**
 * 그 나라의 **마지막 계산일**. 종목의 마지막 신호가 이보다 옛날이면 "오늘은 신호 없음" 이다.
 *
 * **정의처는 `lib/recommend.LAST_CALC_DATE` 하나다** (docs/infra.md 25.145).
 * 전에는 여기도 `signals` 의 MAX 를 따로 적고 있었다 — 한 건도 안 걸린 날에는
 * 그 표에 새 행이 없어 **어제 날짜**로 판정표를 찾았고, 판정표는 오늘 것만 남으므로
 * 화면이 "왜 신호가 없나" 를 **바로 그날** 못 보여 줬다.
 */
export const SIGNALS_LATEST_DATE = LAST_CALC_DATE;
export { LAST_CALC_DATE_FALLBACK };

/**
 * 그 종목·그 기준일의 신호. **그 나라·그 기준일의 가장 새 판만** (docs/infra.md 25.423·25.466) — 새 판이 이 종목을
 * 걸러 냈으면 옛 판 행이 남아 있어도 "신호 없음" 이다. 인자: [종목, 날짜, 나라, 날짜] (판 하위질의는 바깥 행을
 * 가리키지 않는다, 25.428)
 */
export const SIGNALS = `SELECT horizon, signal_type, buy_zone_low, buy_zone_high, currency, tranche_plan, target_price, stop_price,
  suggested_weight_pct, suggested_amount, size_reduction, rationale_text, rationale_data, as_of_date, calc_version
FROM signals WHERE stock_id = ? AND as_of_date = ?
  AND calc_version = (SELECT MAX(c.calc_version) FROM signals c JOIN stocks s3 ON s3.id = c.stock_id
    WHERE s3.country = ? AND c.as_of_date = ?)
ORDER BY CASE horizon WHEN 'short' THEN 0 WHEN 'mid' THEN 1 ELSE 2 END`;

/** 이 종목에 신호 행이 있는 날과 그날 이 종목의 가장 새 판. 최근부터 — `lastSignalDate` 가 앞에서부터 확인한다 */
export const OWN_SIGNAL_DATES = `SELECT as_of_date AS d, MAX(calc_version) AS v FROM signals WHERE stock_id = ?
GROUP BY as_of_date ORDER BY as_of_date DESC LIMIT 30`;

/** 그 나라·그 기준일의 가장 새 판. 인자: [나라, 날짜] */
export const MAX_SIGNAL_VERSION_ON = `SELECT MAX(c.calc_version) AS v FROM signals c JOIN stocks s3 ON s3.id = c.stock_id
WHERE s3.country = ? AND c.as_of_date = ?`;

/**
 * 이 종목의 **마지막 신호일** — 그날의 가장 새 판에 이 종목이 있던 날 (docs/infra.md 25.466).
 * 예전(`MAX(as_of_date)`)에는 새 판이 걸러 낸 옛 판 행의 날짜를 "마지막 신호" 로 보여 줬다. 한 질의로 쓰면 날마다 판을
 * 다시 세는 상관 하위질의가 된다(25.428 의 277배) — 최근 날부터 하나씩 확인한다. 대개 첫 날에서 끝난다
 */
export async function lastSignalDate(exec: Exec, id: number, country: string): Promise<string | null> {
  for (const r of await exec(OWN_SIGNAL_DATES, [id])) {
    const 그날판 = (await exec(MAX_SIGNAL_VERSION_ON, [country, String(r.d)]))[0]?.v;
    if (그날판 != null && Number(r.v) === Number(그날판)) return String(r.d);
  }
  return null;
}

/** 판정표 (docs/signals.md 9장). 신호가 없는 기간에 "왜 없나" 를 기준별로 보여 준다 */
export const SIGNAL_CHECKS = `SELECT horizon, passed, failed_count, checks_json, as_of_date
FROM signal_checks WHERE stock_id = ? AND as_of_date = ?
ORDER BY CASE horizon WHEN 'short' THEN 0 WHEN 'mid' THEN 1 ELSE 2 END`;

// ---------------------------------------------------------------------------
// 고르기
// ---------------------------------------------------------------------------

/** 키마다 첫 행만. 질의가 이미 원하는 순서로 정렬해 둔다 */
export function firstBy<T extends Row>(rows: T[], key: keyof T): T[] {
  const seen = new Set<unknown>();
  return rows.filter((r) => {
    if (seen.has(r[key])) return false;
    seen.add(r[key]);
    return true;
  });
}

/** 연도마다 한 행(연결 우선), 최근 5개 연도, 오래된 해부터 */
export function pickAnnual(rows: Row[], years = 5): Row[] {
  return firstBy(rows, "fiscal_year").slice(0, years).reverse();
}

const isMissingTable = (e: unknown) => e instanceof Error && /no such table/i.test(e.message);

async function guard(run: () => Promise<SectionResult>): Promise<SectionResult> {
  try {
    return await run();
  } catch (error) {
    if (isMissingTable(error)) return empty("표가 아직 없습니다. 배치를 한 번 돌리면 만들어집니다");
    throw error;
  }
}

// ---------------------------------------------------------------------------
// 섹션
// ---------------------------------------------------------------------------

export async function loadOverview(exec: Exec, id: number) {
  const master = (await exec(MASTER, [id]))[0];
  if (!master) return null;
  // **하나가 실패해도 머리 전체를 버리지 않는다** (docs/infra.md 25.590, 감사). 예전에는 표가 없을 때만 삼키고 다른 오류는 던져, 매도 플래그
  // 질의 하나가 503 이면 따로 잘 읽힌 가격·신호·재무 9개 구역까지 화면 전체가 빨간 한 줄이 됐다(stock_detail.md 1장 "한 섹션이 실패해도
  // 나머지는 보여야 한다"). 못 읽은 것은 `unread` 에 이름을 모아 화면이 "읽지 못했습니다" 라고 말한다 — "없다" 로 만들지 않는다
  const unread: string[] = [];
  const optional = async (sql: string, args: Array<string | number>, 이름: string) => {
    try {
      return await exec(sql, args);
    } catch (error) {
      if (isMissingTable(error)) return [];
      unread.push(이름);
      return [];
    }
  };
  // 곁다리 읽기: 실패하면 null(모름). 요약 표가 없으면(0023 전) 빈 목록 — 아직 계산한 적 없음
  const 읽거나_모름 = async (sql: string, 표없으면_빈것: boolean): Promise<Row[] | null> => {
    try {
      return await exec(sql, []);
    } catch (error) {
      return 표없으면_빈것 && isMissingTable(error) ? [] : null;
    }
  };
  const [score, factors, position, flags, watch, 요약판, 지금판] = await Promise.all([
    optional(LATEST_SCORE, [id], "종합 점수"),
    optional(LATEST_FACTORS, [id, id], "팩터"),
    optional(POSITION, [id], "보유"),
    optional(STOCK_FLAGS, [id], "매도 플래그"),
    optional(WATCH, [id], "관심 종목 여부"),
    // 보유가 매매 기록보다 늦은지 (docs/infra.md 25.806). 못 읽으면 모른다(null) — "보유" 이름으로 모으지 않는다
    읽거나_모름(POSITION_SUMMARY_VERSION, true),
    읽거나_모름(TRADES_VERSION, false),
  ]);
  const 늦음 = positionStale(요약판, 지금판);
  // 늦었으면 **멈춘 것인지** 본다 — 포트폴리오 화면과 같은 판정(`recalcStuckNote`, 25.554). 재계산이 실패해도 "보통 1~2분" 띠가
  // 끝없이 떴다 (25.809, 교차검증). 못 읽으면 예전처럼 "계산 중" 이다
  let position_stuck: string | null = null;
  if (늦음) {
    const [편집, 실행] = await Promise.all([읽거나_모름(LAST_EDIT, false), 읽거나_모름(LAST_RECALC_RUN, true)]);
    if (편집 && 실행) {
      type Run = { status: string; started_at: string | null; error_text: string | null };
      const 요약시각 = 요약판?.[0]?.created_at ? String(요약판[0].created_at) : null;
      position_stuck = recalcStuckNote(
        true, (편집[0]?.u as string | null | undefined) ?? null, (실행[0] as Run | undefined) ?? null, new Date(), 요약시각,
      );
    }
  }
  const s = score[0];
  return {
    stock: master,
    score: s ? { ...s, factor_scores: parseJson(s.factor_scores, {}), weights: parseJson(s.weights_json, {}) } : null,
    factors: firstBy(factors, "factor").map((f) => ({
      ...f,
      raw: parseJson(f.raw_json, {}),
      missing: parseJson<string[]>(f.missing_fields, []),
    })),
    position: position[0] ?? null,
    position_stale: 늦음,
    position_stuck,
    flags,
    watch: watch[0] ?? null,
    unread,
  };
}

export async function loadSection(
  exec: Exec,
  section: Section,
  id: number,
  opts: { range?: keyof typeof RANGES; country?: string; today?: string } = {},
): Promise<SectionResult> {
  switch (section) {
    case "prices":
      return guard(async () => {
        const last = (await exec(LAST_PRICE_DATE, [id]))[0]?.last as string | null;
        if (!last) return empty("가격이 아직 수집되지 않았습니다", { range: opts.range ?? "1Y", rows: [] });
        const range = opts.range ?? "1Y";
        const rows = await exec(PRICES, [id, rangeStart(range, last)]);
        return { as_of: last, empty: rows.length === 0, reason: null, range, rows, source: rows.at(-1)?.source ?? null };
      });

    case "financials":
      return guard(async () => {
        const rows = pickAnnual(await exec(FINANCIALS, [id]));
        if (!rows.length) return empty("사업보고서(연간) 재무가 없습니다", { rows: [] });
        const last = rows.at(-1)!;
        return { as_of: String(last.report_date), empty: false, reason: null, rows };
      });

    case "valuation":
      return guard(async () => {
        const band = (await exec(VALUATION, [id]))[0];
        if (!band) return empty("밸류에이션 밴드가 아직 계산되지 않았습니다 (주 1회, 유니버스 편입 종목만)", { band: null });
        const hasBand = band.p50 !== null && band.p50 !== undefined;
        return {
          as_of: String(band.as_of_date),
          empty: !hasBand && band.current_value === null,
          reason: hasBand ? null : String(band.skip_reason ?? "밴드를 만들지 못했습니다"),
          band,
        };
      });

    case "metrics":
      return guard(async () => {
        const rows = firstBy(await exec(METRICS, [id, id]), "window");
        const order = ["1Y", "3Y", "5Y"];
        rows.sort((a, b) => order.indexOf(String(a.window)) - order.indexOf(String(b.window)));
        if (!rows.length) return empty("성과 지표가 아직 계산되지 않았습니다 (주 1회)", { rows: [] });
        return { as_of: String(rows[0].as_of_date), empty: false, reason: null, rows };
      });

    case "news":
      return guard(async () => {
        const [articles, trend] = await Promise.all([exec(NEWS, [id]), exec(SENTIMENT_TREND, [id])]);
        const series = [...trend].reverse();
        if (!articles.length && !series.length) {
          // **왜 없는지** 말한다 (docs/infra.md 25.835, 감사). 두 글자 이름 안내(25.747)는 뉴스 칸이 접히면 그려지지 않아, 고친 대상 종목에서
          // 한 번도 보이지 않았다. 수집 대상이 아닌 종목도 "수집된 뉴스가 없습니다" 라 받고 있는지조차 가를 수 없었다
          // 까닭 읽기가 실패하면 예전 일반 문구로 — 뉴스 칸 전체를 오류로 만들지 않는다 (25.838)
          let 종목: Row | undefined;
          try {
            종목 = (await exec(NEWS_TARGET_STATE, [id, id, id, id]))[0];
          } catch {
            return empty("수집된 뉴스가 없습니다", { articles: [], trend: [] });
          }
          const 이름말 = unmatchedNameNote(String(종목?.country ?? opts.country ?? ""), (종목?.name_ko as string | null | undefined) ?? null);
          const 사유 = 이름말
            ?? (Number(종목?.targeted) === 1 ? "수집 대상인데 최근 기사가 없었습니다" : "뉴스 수집 대상(점수 상위·보유·관심)이 아니라 모으지 않습니다");
          return empty(사유, { articles: [], trend: [] });
        }
        const asOf = (trend[0]?.as_of_date as string | undefined) ?? (articles[0]?.published_at as string | undefined) ?? null;
        return { as_of: asOf, empty: false, reason: null, articles, trend: series, latest: trend[0] ?? null };
      });

    case "quarterly":
      return guard(async () => {
        const rows = (await exec(QUARTERLY, [id])).reverse();  // 오래된 것부터
        if (!rows.length) {
          return empty("분기·반기 재무가 없습니다 (국내 DART 만 수집합니다)", { rows: [] });
        }
        return { as_of: String(rows.at(-1)!.report_date), empty: false, reason: null, rows };
      });

    case "dividends":
      return guard(async () => {
        const rows = await exec(DIVIDENDS, [id]);
        if (!rows.length) return empty(
          // 미국도 받는다 — `us_financials` 가 SEC 공시의 배당을 넣는다 (docs/infra.md 25.381)
          "배당 자료가 없습니다 (국내는 DART 사업보고서, 미국은 SEC 연간 공시에서 받습니다. 배당 태그가 없는 해는 무배당으로 봅니다)",
          { rows: [] },
        );
        return { as_of: (rows[0].as_of_date as string | null) ?? null, empty: false, reason: null, rows };
      });

    case "opinions":
      return guard(async () => {
        const opinions = await exec(OPINIONS, [id]);
        if (!opinions.length) return empty("증권사 의견은 국내 유니버스·보유·관심 종목에 KIS 로 매일 모읍니다(국내만)", { opinions: [], stats: {} });
        // 성적표가 아직 없으면(첫 금요일 전) 의견만 보인다
        const 증권사 = [...new Set(opinions.map((o) => String(o.broker)))];
        const stats = Object.fromEntries(
          (await exec(BROKER_STATS_FOR, [JSON.stringify(증권사)]).catch(ifMissingTable([] as Row[]))).map((s) => [String(s.broker), s]),
        );
        return { as_of: String(opinions[0].fetched_at ?? ""), empty: false, reason: null, opinions, stats };
      });

    case "events":
      return guard(async () => {
        // **그 종목 시장의 현지 날짜다** (docs/infra.md 25.207). UTC 로 잡으면 한국 0~9시에 어제 발표가
        // "앞으로의 일정" 에 남고, 미국 종목을 한국 날짜로 보면 그날 장 마감 뒤 발표가 먼저 사라진다
        const today = opts.today ?? localDate(opts.country === "US" ? "US" : "KR", new Date());
        const [earnings, disclosures] = await Promise.all([exec(EARNINGS, [id, today]), exec(DISCLOSURES, [id])]);
        if (!earnings.length && !disclosures.length) {
          return empty("실적 일정(야후 예정일·법정 기한 추정)은 주 1회 유니버스 종목에, 공시는 지켜보는 종목(보유·신호·관심)에만 모읍니다", { earnings: [], disclosures: [] });
        }
        const fetched = [...earnings, ...disclosures].map((r) => String(r.fetched_at)).sort().at(-1) ?? null;
        return { as_of: fetched, empty: false, reason: null, earnings, disclosures };
      });

    case "signals":
      return guard(async () => {
        const country = opts.country ?? "KR";
        // 판정표 표가 없는 DB(0034 전)에서는 옛 잣대로 되돌아간다 (docs/infra.md 25.145)
        const latest = (
          await exec(SIGNALS_LATEST_DATE, [country, country]).catch(
            ifMissingTableThen(() => exec(LAST_CALC_DATE_FALLBACK, [country])),
          )
        )[0]?.d as string | null;
        // 최근 신호 실행을 건너뛰었거나 실패했으면 추천 화면처럼 말한다 (docs/infra.md 25.807, 종목 상세 감사) — 상세만 옛 기준일을 말없이 보였다.
        // 실행 기록 표가 없을 때만 말하지 않는다(0005 전)
        const [최근, 성공] = await Promise.all([
          exec(LATEST_SIGNAL_RUN, [country]).catch(ifMissingTable([] as Row[])),
          exec(LAST_SIGNAL_SUCCESS, [country]).catch(ifMissingTable([] as Row[])),
        ]);
        const run_note = signalRunNote(
          최근[0] as unknown as SignalRunRow | undefined, 성공[0]?.finished_at ? String(성공[0].finished_at) : null, latest ?? null,
        );
        if (!latest) {
          return empty(run_note ? `${run_note} · 신호가 아직 계산되지 않았습니다` : "신호가 아직 계산되지 않았습니다", { rows: [], last_signal_date: null, run_note });
        }
        const rows = firstBy(await exec(SIGNALS, [id, latest, country, latest]), "horizon").map((r) => ({ ...r, tranches: parseJson(r.tranche_plan, []), data: parseJson(r.rationale_data, {}) }));
        const lastOwn = await lastSignalDate(exec, id, country);
        // 판정표. **표가 아직 없을 때만** 빈 목록이다(0034 전) — 신호 자체는 보여야 한다.
        // 아무 실패나 삼키면 한도에 걸린 날에도 판정표가 조용히 사라진다 (25.163)
        const checks = await exec(SIGNAL_CHECKS, [id, latest]).catch(ifMissingTable([] as Row[]));
        const judged = checks.map((c) => ({
          horizon: String(c.horizon), passed: Number(c.passed) === 1, failed_count: Number(c.failed_count),
          as_of: String(c.as_of_date), rows: parseJson(c.checks_json, []),
        }));
        if (!rows.length) {
          const 없음 = lastOwn ? `${latest} 기준 신호 없음 (마지막 신호 ${lastOwn})` : `${latest} 기준 신호 없음`;
          const reason = run_note ? `${run_note} · ${없음}` : 없음;
          // 판정표가 있으면 비어 있는 것이 아니다 — "왜 없나" 를 그린다
          return { as_of: latest, empty: judged.length === 0, reason, rows: [], last_signal_date: lastOwn, checks: judged, run_note };
        }
        return { as_of: latest, empty: false, reason: null, rows, last_signal_date: lastOwn, checks: judged, run_note };
      });
  }
}

// ---------------------------------------------------------------------------
// 표시
// ---------------------------------------------------------------------------

export const FACTOR_LABELS: Array<[string, string]> = [
  ["value", "밸류"],
  ["quality", "퀄리티"],
  ["growth", "성장"],
  ["momentum", "모멘텀"],
  ["risk", "안정성"],
];

/**
 * 팩터 구성 지표의 한글 이름. 정의처는 docs/factors.md (3장·10장) 이고 여기는 표시용 사전이다.
 * 사전에 없는 키는 키 그대로 보여 준다 — 새 지표를 더하고 사전을 잊어도 숨지 않는다.
 */
export const METRIC_LABELS: Record<string, string> = {
  ep: "이익수익률(E/P)",
  bp: "순자산수익률(B/P)",
  sp: "매출수익률(S/P)",
  dividend_yield: "배당수익률(D/P)", // 2026-09-21 (docs/factors.md 11.1)
  roe: "ROE",
  roa: "ROA",
  operating_margin: "영업이익률",
  debt_ratio: "부채비율",
  profit_stability: "이익 안정성",
  asset_growth: "자산 성장률",
  current_ratio: "유동비율",
  piotroski_lite: "피오트로스키(축소)",
  revenue_growth: "매출 성장률",
  operating_income_growth: "영업이익 성장률",
  revenue_cagr_3y: "매출 3년 CAGR",
  momentum_12_1: "12-1개월 모멘텀",
  momentum_6m: "6개월 모멘텀",
  momentum_3m: "3개월 모멘텀",
  high_52w_proximity: "52주 고점 근접도",
  momentum_consistency: "모멘텀 꾸준함",
  momentum_vol_adjusted: "변동성 조정 모멘텀",
  mdd_abs: "MDD",
  volatility_ann: "연환산 변동성",
  sharpe: "샤프",
  sortino: "소르티노",
  beta_abs: "베타",
  mdd_recovery_days: "MDD 회복 기간",
  cagr: "CAGR",
  amihud_illiquidity: "Amihud 비유동성",
  // 2026-09-21 (docs/factors.md 11.2·11.3).
  // **이름이 단위를 말해야 한다.** 처음에 "지난 한 달 최대 상승일" 이라고 적었는데
  // 값은 날짜가 아니라 **수익률**이다 — 이름이 단위를 틀리게 말하면 근거표가 거짓말한다
  max_daily_return: "지난 한 달 최대 일간 상승률", // 변동성은 퍼짐, 이것은 한 번의 꼬리다
  idio_volatility: "잔차 변동성(연율)", // 시장으로 설명되지 않는 흔들림. √252 로 연환산한 값이다
};

/**
 * `factors.raw_json` 안에서 **치환 사실**을 담는 예약 키. 파이썬의
 * `services/scoring.SUBSTITUTED_KEY` 와 같은 글자여야 한다 —
 * `web/__tests__/substituted.test.ts` 가 둘이 같은지 본다.
 */
export const SUBSTITUTED_KEY = "_substituted";

/**
 * 밸류 팩터 `raw_json` 안의 **시가총액을 어느 날 값에서 어떻게 옮겼는지** (docs/factors.md 3.1 "분모 시점 규칙", docs/infra.md 25.954).
 * 파이썬 `services/scoring.MARKET_CAP_KEY` 와 같은 글자여야 한다 — `web/__tests__/marketCapNote954.test.ts` 가 둘을 대 본다.
 */
export const MARKET_CAP_KEY = "_market_cap";

/** 근거표의 "시가총액" 한 줄. 키가 없으면(옛 판) "스냅샷 시총 그대로(날짜 미상)" — 지어내지 않는다 */
export function marketCapNote(raw: Record<string, unknown> | null | undefined, currency?: string): string {
  const n = raw?.[MARKET_CAP_KEY];
  if (!n || typeof n !== "object") return "스냅샷 시총 그대로 (날짜 미상 — 분모 시점 규칙 전 계산)";
  const r = n as Record<string, unknown>;
  const cap = typeof r.cap === "number"
    ? `${Math.round(r.cap).toLocaleString("en-US")}${currency === "KRW" ? "원" : currency === "USD" ? "달러" : ""}`
    : "-";
  const 날 = typeof r.cap_date === "string" && r.cap_date ? r.cap_date : "날짜 미상";
  if (r.scaled === true && typeof r.factor === "number") {
    return `${cap} (${날} 값) × 가격 배수 ${r.factor.toFixed(4)} — 기준일 가격으로 옮김`;
  }
  const 사유 = typeof r.reason === "string" && r.reason ? r.reason : "사유 미상";
  return `${cap} (${날} 값) 그대로 — 옮기지 않음: ${사유}`;
}

/**
 * 리스크 축에서 **성과지표 표에 없는** 지표들의 한 줄 (docs/factors.md 3.5).
 *
 * CAGR·MDD·샤프·소르티노·변동성·베타·회복기간은 `performance_metrics` 에서 와
 * 이미 과거 성과 표에 그려진다. 나머지 셋(Amihud·MAX·잔차 변동성)은 가격 계열에서
 * 그 자리에서 내는 값이라 **어느 화면에도 없었다** — 점수에는 들어가는데 근거는
 * 볼 수 없었다는 뜻이다(docs/infra.md 25.93).
 */
export function priceRiskNote(raw: Record<string, unknown>, currency?: string): string {
  const 줄: string[] = [];
  // **유효숫자 셋과 단위** (docs/infra.md 25.941, 감사). 배치는 mean(|일 수익률| ÷ (거래대금 ÷ 10억))(`scoring.amihud_illiquidity`)이라
  // 하루 1조원 대형주는 약 0.000015 — 소수 셋째 자리로 자르면 "0.000" 이 되어 값이 달라도 같아 보였고, 단위(10억 원/달러당)도 없었다
  if (isNum(raw?.amihud_illiquidity)) {
    const v = raw.amihud_illiquidity;
    const 값 = v === 0 ? "0" : Math.abs(v) >= 1 ? formatNum(v, 3) : Number(v.toPrecision(3)).toString();
    const 단위 = currency === "KRW" ? "10억원" : currency === "USD" ? "10억달러" : "거래대금 10억";
    줄.push(`Amihud ${값} (${단위}당 |일 수익률|)`);
  }
  if (isNum(raw?.max_daily_return)) 줄.push(`최대 일간 상승 ${formatPct(raw.max_daily_return)}`);
  if (isNum(raw?.idio_volatility)) 줄.push(`잔차 변동성 ${formatPct(raw.idio_volatility)}`);
  return 줄.join(" · ");
}

/** 근거표에 적을 "치환했다" 한 줄. 치환이 없으면 빈 문자열 (docs/factors.md 3.5) */
export function substitutedNote(raw: Record<string, unknown>): string {
  const 섞인것 = raw?.[SUBSTITUTED_KEY];
  if (!섞인것 || typeof 섞인것 !== "object") return "";
  const 줄 = Object.entries(섞인것 as Record<string, unknown>)
    .filter(([, v]) => typeof v === "number" && Number.isFinite(v))
    // "집단 최악" 이 아니다 (docs/infra.md 25.693, 교차검증) — 25.685·25.691 뒤로 채운 값은 **그만큼 빠졌던 종목들의 최악**과
    // **바닥 뒤 지난 행 수** 가운데 나쁜 쪽이라, 집단에 없는 값(그 종목 자신의 경과)일 수 있다
    .map(([k, v]) => `${metricLabel(k)} → 미회복이라 ${Number(v).toLocaleString()}(으)로 채움 (비슷하게 빠진 종목 최악·바닥 뒤 경과 중 나쁜 쪽)`);
  return 줄.length ? 줄.join(", ") : "";
}

export function metricLabel(key: string): string {
  return METRIC_LABELS[key] ?? key;
}

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** 큰 금액. 원은 조·억, 달러는 B·M. 없으면 "-" */
export function formatAmount(value: unknown, currency: string): string {
  if (!isNum(value)) return "-";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (currency === "KRW") {
    if (abs >= 1e12) return `${sign}${(abs / 1e12).toFixed(1)}조`;
    if (abs >= 1e8) return `${sign}${Math.round(abs / 1e8).toLocaleString("ko-KR")}억`;
    return `${sign}${Math.round(abs).toLocaleString("ko-KR")}원`;
  }
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(0)}M`;
  return `${sign}$${abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** 일정 한 건이 어디서 왔고 확정인지. 화면이 "추정"을 뭉뚱그리지 않게 한다 */
export function eventOrigin(row: Row): string {
  const yahoo = String(row.source ?? "").startsWith("yfinance");
  if (row.is_confirmed) return yahoo ? "야후 예정일" : "확정";
  return yahoo ? "추정: 야후 구간" : "추정: 법정 기한";
}

export function formatPrice(value: unknown, currency: string): string {
  if (!isNum(value)) return "-";
  return currency === "KRW"
    ? `${Math.round(value).toLocaleString("ko-KR")}원`
    : `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** 비율(0.123) → "12.3%" */
export function formatPct(value: unknown, digits = 1): string {
  return isNum(value) ? `${(value * 100).toFixed(digits)}%` : "-";
}

/**
 * **이미 퍼센트인 숫자**(10 = 10%)를 적는다 (docs/infra.md 25.483). `scores.sentiment_weight_used` 는 설정값 그대로 10 으로
 * 저장된다 — 비율을 받는 `formatPct` 로 적으면 "1000%" 가 됐다(추천 카드 `sentimentLine` 은 10% 로 맞게 적었다)
 */
export function formatPercentNumber(value: unknown, digits = 0): string {
  return isNum(value) ? `${value.toFixed(digits)}%` : "-";
}

export function formatNum(value: unknown, digits = 2): string {
  return isNum(value) ? value.toFixed(digits) : "-";
}

export interface OverlayLine {
  price: number;
  label: string;
  kind: "zone" | "target" | "stop" | "cost";
}

/** 차트에 겹칠 선. 저장된 값만 쓰고 없는 값은 선을 긋지 않는다 */
export function overlaysFor(signal: Row | null, position: Row | null): OverlayLine[] {
  const out: OverlayLine[] = [];
  if (signal) {
    if (isNum(signal.buy_zone_high)) out.push({ price: signal.buy_zone_high, label: "매수 구간 위", kind: "zone" });
    if (isNum(signal.buy_zone_low)) out.push({ price: signal.buy_zone_low, label: "매수 구간 아래", kind: "zone" });
    if (isNum(signal.target_price)) out.push({ price: signal.target_price, label: "목표", kind: "target" });
    if (isNum(signal.stop_price)) out.push({ price: signal.stop_price, label: "손절", kind: "stop" });
  }
  if (position && isNum(position.avg_price)) out.push({ price: position.avg_price, label: "내 평균 단가", kind: "cost" });
  return out;
}

/**
 * 가격 옆 조정 표기 (docs/infra.md 25.485, 웹 감사). 국내는 한국거래소 원자료(분할 전 가격 그대로)이고, 미국은 야후 Close 라
 * **분할은 반영돼 있고 배당은 빠져 있다**(docs/adjust.md 7장). 예전에는 둘 다 "수정주가 아님" 이라 미국에서는 틀린 말이었다
 */
export function priceAdjustLabel(country: string | null | undefined): string {
  // 미국: 받을 때의 분할만 반영된다 — 일일 배치는 최근 10일만 덮어써서 **분할 전에 받은 옛 행은 조정 전 값**으로 남고,
  // 어긋남 감지(adjust_drift)는 수정종가/종가 비율만 봐 분할을 못 잡는다 (25.487, 교차검증)
  return country === "US" ? "야후 종가(받은 날 기준 분할 반영, 옛 행은 조정 전일 수 있음 · 배당 미반영)" : "원자료(수정주가 아님)";
}

/** 배당 표 밑의 출처 한 줄 (25.485). 가장 늦게 받은 행의 출처·받은 시각 — 분기 재무 표처럼 보여 준다 */
export function dividendSourceLine(rows: Row[], userTimeOf: (ts: string) => string): string | null {
  const 최근 = [...rows].filter((r) => r.fetched_at).sort((a, b) => String(b.fetched_at).localeCompare(String(a.fetched_at)))[0];
  if (!최근) return null;
  return `출처 ${String(최근.source ?? "-")} · 받은 시각 ${userTimeOf(String(최근.fetched_at))} KST`;
}

/**
 * 가격 차트 아래 "겹쳐 그린 선" 문장 (docs/infra.md 25.483). 선이 없을 때 **왜 없는지**를 사실대로 말한다.
 * 예전에는 신호 섹션이 읽는 중이거나 실패해도(한도·500) 늘 "오늘 신호가 없고 보유하지 않은 종목입니다" 였다 —
 * 못 읽은 것을 없는 것으로, 마지막 계산일 기준을 "오늘" 로, 평균 단가만 빈 보유 종목을 "보유하지 않은" 으로 적었다.
 */
export function overlayNote(
  overlays: OverlayLine[], signalsState: "loading" | "error" | "ok", signalAsOf: string | null, position: Row | null,
  currency: string, formatPrice: (v: unknown, c: string) => string,
  보유상태: "ok" | "unread" | "stale" = "ok",
): string {
  // 보유를 못 읽었거나 재계산 전이면 "보유하지 않은 종목" 이라 하지 않는다 (docs/infra.md 25.806, 종목 상세 감사)
  const 보유말 = 보유상태 === "unread" ? "보유는 읽지 못해 평균 단가 선을 그리지 않았습니다"
    : 보유상태 === "stale" ? "매매 기록이 바뀌어 보유를 다시 계산하는 중이라 평균 단가 선을 그리지 않았습니다" : null;
  // 평균 단가 선만 있어도 신호를 못 읽었으면 그것을 함께 말한다 (25.484, 교차검증)
  const 신호상태 = signalsState === "loading" ? "신호를 읽는 중입니다"
    : signalsState === "error" ? "신호를 읽지 못해 신호 선은 그리지 않았습니다 (아래 신호 칸의 오류를 보세요)" : null;
  if (overlays.length > 0) {
    // 신호 선은 어느 날 계산인지 함께 적는다 — 건너뛴 날엔 어제 구간이다 (25.811, 종목 상세 감사)
    const 신호선 = overlays.some((o) => o.kind !== "cost") && signalAsOf ? ` (신호 ${signalAsOf} 기준)` : "";
    const 선 = `선${신호선}: ${overlays.map((o) => `${o.label} ${formatPrice(o.price, currency)}`).join(" · ")}`;
    const 덧 = [신호상태, 보유말].filter(Boolean).join(" · ");
    return 덧 ? `${선} — ${덧}` : 선;
  }
  if (신호상태) return 보유말 ? `${신호상태} · ${보유말}` : 신호상태;
  const 신호 = signalAsOf ? `${signalAsOf} 기준 신호 없음` : "신호 없음";
  const 보유 = 보유말 ?? (position ? "보유 중이지만 평균 단가가 없어 선이 없습니다" : "보유하지 않은 종목입니다");
  return `겹쳐 그린 선 없음 — ${신호}, ${보유}`;
}

/**
 * 점수와 신호의 기준일이 다르면 한 줄 (docs/infra.md 25.807, 종목 상세 감사). 점수는 성공했는데 신호를 건너뛴 날, 위 점수 칸과 아래 신호 칸이
 * 서로 다른 날의 것인데 말이 없었다. 같거나 둘 중 하나가 없으면 null
 */
export function signalScoreDateNote(signalAsOf: string | null, scoreAsOf: string | null): string | null {
  if (!signalAsOf || !scoreAsOf) return null;
  const 신호 = signalAsOf.slice(0, 10);
  const 점수 = scoreAsOf.slice(0, 10);
  // **신호가 점수보다 옛 날일 때만** — 점수가 더 묵은 것은 점수 칸의 묵음 경고(25.811)가 말한다. 신호 기준일은 나라 전체 최신일이라
  // 이 종목만 거래정지로 점수가 묵어도 이 줄이 떴다 (25.812, 교차검증)
  return 신호 < 점수 ? `신호는 ${신호} 기준, 위 점수는 ${점수} 기준입니다 — 서로 다른 날의 계산입니다` : null;
}

/** 밴드 막대의 가로 위치(0~100). 20·80 분위와 현재값을 모두 담도록 양 끝을 10% 넓힌다 */
export function bandPositions(band: Row): Record<"p20" | "p30" | "p50" | "p80" | "current", number | null> | null {
  const keys = ["p20", "p30", "p50", "p80", "current_value"] as const;
  const values = keys.map((k) => band[k]).filter(isNum);
  if (values.length < 2) return null;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pad = (hi - lo) * 0.1 || Math.abs(hi) * 0.1 || 1;
  const pos = (v: unknown) => (isNum(v) ? ((v - (lo - pad)) / (hi - lo + 2 * pad)) * 100 : null);
  return { p20: pos(band.p20), p30: pos(band.p30), p50: pos(band.p50), p80: pos(band.p80), current: pos(band.current_value) };
}

/** 팩터 레이더 꼭짓점. 점수가 없는 축은 0 에 찍고 missing 으로 알린다 */
export function radarPoints(scores: Record<string, unknown>, radius: number, center: number) {
  return FACTOR_LABELS.map(([key, label], i) => {
    const angle = -Math.PI / 2 + (i * 2 * Math.PI) / FACTOR_LABELS.length;
    const score = isNum(scores[key]) ? Math.max(0, Math.min(100, scores[key] as number)) : null;
    const r = ((score ?? 0) / 100) * radius;
    return {
      key,
      label,
      score,
      x: center + r * Math.cos(angle),
      y: center + r * Math.sin(angle),
      axisX: center + radius * Math.cos(angle),
      axisY: center + radius * Math.sin(angle),
    };
  });
}

/**
 * 성과 표의 "회복" 칸 (docs/infra.md 25.333).
 *
 * 배치는 표본이 모자라면(1Y 200·3Y 600·5Y 1000행 미만) 값을 전부 NULL 로 둔 행을 저장한다. 예전 화면은
 * `mdd_recovery_days` 가 숫자가 아니면 무조건 "미회복" 이라 적어, 계산하지 않은 5Y 열에 **부정적 사실**을 지어냈다.
 * MDD 가 없으면 회복도 모른다("-"). MDD 가 있는데 회복 일수가 없을 때만 "미회복" 이다.
 */
export function recoveryText(r: { mdd?: unknown; mdd_recovery_days?: unknown }): string {
  if (typeof r.mdd !== "number") return "-";
  return typeof r.mdd_recovery_days === "number" ? `${r.mdd_recovery_days}일` : "미회복";
}

/**
 * 점수 칸의 센티먼트가 비었을 때 **왜** 비었나 (docs/infra.md 25.381 뒤, 25.382).
 *
 * `scores.sentiment_score` 는 "종합 점수에 실제로 들어간 값" 이라 감성이 멀쩡히 있어도 비는 길이 셋 더 있다 —
 * 가중치가 0, 종합 점수를 내지 않음(팩터 결측·범위 밖 가중치, `skip_reason`). 예전에는 늘 "기사 5건 미만이거나
 * 3일 넘게 갱신되지 않음" 이라 적어, 같은 화면 뉴스 칸의 "감성 40" 과 서로 다른 말을 했다.
 */
export function sentimentEmptyReason(score: Record<string, unknown>): string {
  if (score.total_score === null || score.total_score === undefined || score.skip_reason) {
    return "종합 점수를 내지 않아 센티먼트도 반영되지 않았습니다 — 감성 값 자체는 아래 뉴스 칸에서 봅니다";
  }
  // **배치는 감성이 없을 때도 쓴 가중치를 0 으로 저장한다**(`scoring.total_score`, 열이 NOT NULL) — 0 만으로는 "설정이 0" 과
  // "반영할 값이 없었다" 를 가를 수 없다. 예전 문구는 늘 "설정의 가중치가 0" 이라 기사 부족·묵음인 대부분의 경우에 틀렸다 (25.833, 감사)
  return "종합 점수에 반영하지 않았습니다 — 감성 값이 없었거나(최근 30일 기사 5건 미만, 7일 넘게 새 기사 없음, 채점 안 된 기사가 많음, 3일 넘게 갱신 안 됨) 설정의 센티먼트 가중치가 0 입니다. 감성 값은 아래 뉴스 칸에서 봅니다";
}

/**
 * 감성 행의 값이 비었을 때의 까닭 (docs/infra.md 25.838, 교차검증). 예전에는 늘 "없음(기사 5건 미만)" 이라, 25.832·25.834 뒤에는
 * "없음(기사 5건 미만) · 30일 기사 12건" 같은 자기모순이 나왔다. 기사 수로 가른다(배치 `services/sentiment` 의 문턱과 같다)
 */
export const SENTIMENT_MIN_ARTICLES = 5;
export function sentimentNullReason(articleCount: unknown): string {
  return Number(articleCount) < SENTIMENT_MIN_ARTICLES
    ? `없음(기사 ${SENTIMENT_MIN_ARTICLES}건 미만)`
    : "없음(7일 넘게 새 기사 없음 또는 채점 안 된 기사가 많음)";
}

/** 리스크 팩터가 쓴 성과 지표의 창·기준일 — 배치 `scoring.RISK_SOURCE_KEY` 와 같은 글자 (docs/infra.md 25.383) */
export const RISK_SOURCE_KEY = "_metrics_source";

/** "성과 지표 3Y (2026-09-25 기준)". 25.383 전 행에는 없다 — 그때는 아무 말도 하지 않는다 */
export function riskSourceNote(raw: Record<string, unknown> | null | undefined): string | null {
  const src = raw?.[RISK_SOURCE_KEY] as { window?: unknown; as_of_date?: unknown } | undefined;
  if (!src || typeof src.window !== "string") return null;
  return `성과 지표 ${src.window}${typeof src.as_of_date === "string" ? ` (${src.as_of_date} 기준)` : ""}`;
}

/** 배치 `services.sentiment.MAX_AGE_DAYS` 와 같다 — 이보다 묵은 감성은 점수에 쓰지 않는다 (25.590) */
export const SENTIMENT_MAX_AGE_DAYS = 3;

/**
 * 뉴스 칸 감성이 묵었나 (docs/infra.md 25.590, 감사). 예전에는 7월 1일 감성 +45 를 굵게 보이고 7일 변화까지 붙여, 같은 화면 점수 칸
 * ("3일 넘게 갱신되지 않음")과 말이 달랐다 — 25.577 이 스크리너에서 고친 것과 같은 모양. 묵었으면 날짜와 함께 "점수에 쓰지 않음" 을 적는다
 */
export function staleSentimentNote(asOf: string | null | undefined, basisDate: string): string | null {
  if (!asOf) return null;
  const 날 = (Date.parse(`${basisDate}T00:00:00Z`) - Date.parse(`${asOf.slice(0, 10)}T00:00:00Z`)) / 86_400_000;
  if (!Number.isFinite(날) || 날 <= SENTIMENT_MAX_AGE_DAYS) return null;
  return `${asOf.slice(0, 10)} 감성입니다 — ${SENTIMENT_MAX_AGE_DAYS}일 넘게 갱신되지 않아 종합 점수에는 쓰지 않습니다`;
}

/** 점수 기준일이 이만큼 넘게 묵었으면 점수가 멈춘 것으로 본다 — 국내 최장 연휴 11일보다 길게 (25.596) */
export const SCORE_STALE_DAYS = 14;

/**
 * 점수 칸 머리의 "멈춘 점수" 경고 (docs/infra.md 25.811, 종목 상세 감사). 감성 줄은 25.594 부터 이 문턱으로 묵음을 가렸는데, 점수 자체에는
 * 기준일만 작게 있어 유니버스에서 빠졌거나 배치가 멈춘 종목의 7월 점수가 지금 점수처럼 보였다. `today` 는 그 종목 시장의 오늘
 */
export function scoreStaleNote(scoreAsOf: string | null | undefined, today: string): string | null {
  if (!scoreAsOf) return null;
  const 일 = (Date.parse(`${today}T00:00:00Z`) - Date.parse(`${String(scoreAsOf).slice(0, 10)}T00:00:00Z`)) / 86_400_000;
  if (!(일 > SCORE_STALE_DAYS)) return null;
  return `${Math.floor(일)}일 전 점수입니다 — 그 뒤로 다시 계산되지 않았습니다(유니버스에서 빠졌거나 배치가 멈춤)`;
}


/**
 * ETF 상세에서 **보통주 전용 구역**이 비었을 때의 까닭 (docs/infra.md 25.940, 감사). 25.905 는 점수 카드만 고쳤고, 나머지는 보통주의
 * 까닭("아직 계산되지 않았습니다", "배당 태그가 없는 해는 무배당", "D 기준 신호 없음")을 그대로 보여 — ETF 를 주식으로 평가해 떨어뜨렸거나
 * 분배금이 없는 것처럼 읽혔다. ETF 는 유니버스·재무·배당 수집 밖이다(25.896·25.906)
 */
export const ETF_SECTION_NOTE: Record<"signals" | "valuation" | "financials" | "quarterly" | "dividends", string> = {
  signals: "ETF 는 매수 신호를 내지 않습니다 — 장기 적립 관점의 판정은 ETF 탭에 있습니다",
  valuation: "ETF 는 밸류에이션 밴드를 계산하지 않습니다",
  financials: "ETF 는 재무제표가 없습니다",
  quarterly: "ETF 는 재무제표가 없습니다",
  dividends: "ETF 분배금은 이 앱이 모으지 않습니다 — 분배가 없다는 뜻이 아닙니다",
};
