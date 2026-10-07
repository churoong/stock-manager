/**
 * 매매 복기 조회 (docs/review.md, Step 15).
 *
 * 웹은 **읽기만 한다**. trade_reviews · review_stats 는 batch/jobs/portfolio.py 가 trade_lots 와 같은
 * 실행에서 통째로 다시 만든다. 화면은 저장된 수치를 그대로 그리고, 더하거나 나누지 않는다
 * (CLAUDE.md "웹앱은 계산하지 않는다").
 */

// 비용 추정 여부는 FIFO 결과(trade_lots)에 이미 있다 — 복기 표에 열을 더하지 않고 읽어 붙인다 (docs/infra.md 25.711, 감사).
// **한 번 묶어 붙인다** (25.716, 교차검증) — 상관 서브쿼리는 `trade_lots.buy_trade_id` 인덱스가 없어 복기 행마다 표를 훑어 D1 읽기
// 행이 (복기 수 × 체결 묶음 수)로 곱해졌다(400×800 에 VM 스텝 41배). 묶음은 표를 한 번만 읽는다
export const REVIEWS = `SELECT r.*, s.ticker, s.country, COALESCE(s.name_ko, s.name_en, s.ticker) AS name,
  COALESCE(e.cost_estimated, 0) AS cost_estimated
FROM trade_reviews r JOIN stocks s ON s.id = r.stock_id
LEFT JOIN (SELECT buy_trade_id, MAX(cost_estimated) AS cost_estimated FROM trade_lots GROUP BY buy_trade_id) e
  ON e.buy_trade_id = r.buy_trade_id
ORDER BY r.last_sell_date DESC, r.buy_trade_id DESC
LIMIT 301`;

/**
 * 복기 목록에 보이는 최대 행 수. 질의는 **하나 더** 읽어 넘쳤는지 안다 (docs/infra.md 25.936, 감사) — 예전에는 300 에서 말없이 잘려
 * 탭 숫자(300)와 요약 통계(전체)가 같은 화면에서 어긋났다
 */
export const REVIEW_LIMIT = 300;

/** 전체 → 기간 → 신호 순. 배치가 넣은 순서와 같다(review.build_stats) */
export const REVIEW_STATS = `SELECT * FROM review_stats
ORDER BY CASE group_kind WHEN 'all' THEN 0 WHEN 'horizon' THEN 1 ELSE 2 END,
  CASE group_key WHEN 'horizon:short' THEN 0 WHEN 'horizon:mid' THEN 1 WHEN 'horizon:long' THEN 2 ELSE 3 END,
  label`;

export interface ReviewRow {
  buy_trade_id: number; stock_id: number; ticker: string; name: string; country: string;
  horizon: string | null; buy_date: string; last_sell_date: string; currency: string;
  quantity_sold: number; quantity_bought: number; partial: number;
  cost: number; proceeds: number; return_pct: number; holding_days: number;
  realized_pnl_krw: number; price_pnl_krw: number; fx_pnl_krw: number;
  has_snapshot: number; snapshot_as_of: string | null; score_at_trade: number | null;
  signal_type_at_trade: string | null; sentiment_at_trade: number | null; factor_scores_at_trade: string | null;
  target_pct: number | null; stop_pct: number | null; outcome: string; verdict_text: string;
  calc_version: number; created_at: string;
  /** 수수료·세금 중 추정값이 섞였나 (0/1). 이익이 0 근처면 승패가 추정치에 따라 뒤집힐 수 있다 */
  cost_estimated: number;
}

export interface ReviewStat {
  group_key: string; group_kind: "all" | "horizon" | "signal"; label: string; n: number; sample_ok: number;
  win_rate: number | null; avg_return_pct: number | null; median_return_pct: number | null;
  avg_holding_days: number | null; target_rate: number | null; stop_rate: number | null; total_pnl_krw: number;
  calc_version: number; created_at: string;
}

/**
 * 복기 한 줄의 원화 손익 글. 해외 종목은 **주가와 환을 나눠** 적는다 (docs/infra.md 25.241).
 *
 * `return_pct` 는 종목 통화 기준이다(docs/review.md 1장) — 판정(목표·손절)이 주가로 한다. 원화 손익은 매도일 환율로 환산해
 * 환차손익을 포함한다. 둘을 나란히 적기만 하면 $100 → $104 에 팔고 환율이 1,400 → 1,300 으로 내린 매수가
 * "+4.0% −4,800원" 으로 보여 부호가 서로 반대다. CLAUDE.md "해외 종목은 환차손익을 주가 손익과 분리해서 표시".
 */
export function reviewPnlText(
  r: Pick<ReviewRow, "currency" | "realized_pnl_krw" | "price_pnl_krw" | "fx_pnl_krw"> & { cost_estimated?: number },
  signedWon: (v: number) => string,
): string {
  // 실현손익 탭처럼 "(비용 일부 추정)" 을 붙인다 (25.711, 감사) — 복기 수익률에도 추정 수수료·세금이 들어 있는데 말이 없었다
  const 추정 = r.cost_estimated ? " (비용 일부 추정)" : "";
  if (r.currency === "KRW") return `${signedWon(r.realized_pnl_krw)}${추정}`;
  return `원화 ${signedWon(r.realized_pnl_krw)} (주가 ${signedWon(r.price_pnl_krw)} · 환 ${signedWon(r.fx_pnl_krw)})${추정}`;
}

/** review.outcome_of 의 다섯 판정 + 기간 없음 */
export const OUTCOME_LABEL: Record<string, string> = {
  target: "목표 도달",
  gain: "이익 (목표 미달)",
  flat: "본전",
  loss: "손실 (손절 위)",
  stop: "손절 이탈",
  none: "기간 없음",
};

export const OUTCOME_TONE: Record<string, string> = {
  target: "bg-rose-50 text-rose-800 dark:bg-rose-950 dark:text-rose-200",
  gain: "bg-rose-50/60 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300",
  flat: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  loss: "bg-blue-50/60 text-blue-700 dark:bg-blue-950/60 dark:text-blue-300",
  stop: "bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-200",
  none: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
};

const FACTOR_LABEL: Record<string, string> = {
  value: "밸류", quality: "퀄리티", growth: "성장", momentum: "모멘텀", risk: "리스크",
};

/** 스냅샷의 팩터 점수 JSON 을 [이름, 값] 로. 깨진 JSON 은 빈 목록(표시만 빠진다) */
export function factorEntries(raw: string | null): Array<[string, number]> {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return Object.entries(parsed)
      .filter((e): e is [string, number] => typeof e[1] === "number")
      .map(([k, v]) => [FACTOR_LABEL[k] ?? k, v]);
  } catch {
    return [];
  }
}

/** 표본 부족 문구. 배치가 sample_ok 로 판정하고, 화면은 그 결과만 읽는다 */
export function sampleNote(stat: Pick<ReviewStat, "n" | "sample_ok">): string | null {
  // 0건은 "다 판 매수가 아직 없다" 다 — 일부 매도만 있는 집단은 실현손익만 있고 비율은 비어 있다 (docs/infra.md 25.694·25.704)
  if (stat.n === 0) return "끝난 매수 0건 — 실현손익만 있고 비율은 아직 없다";
  return stat.sample_ok ? null : `표본 ${stat.n}건 — 단정하지 않는다`;
}
