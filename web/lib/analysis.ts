/**
 * 종목 분석 (docs/analysis.md, docs/infra.md 25.1016). 의견은 일일 배치가 만든다 — 여기서는 모양과 글자만 정한다.
 */

export type VerdictKey = "check_holding" | "consider_buy" | "hold" | "waiting" | "undecided";

export interface EvidenceRow {
  label: string;
  display: string | null;
  threshold: string | null;
  source: string | null;
  as_of: string | null;
}

export interface Verdict {
  verdict: VerdictKey;
  headline: string;
  detail: { label?: string; reasons?: string[]; against?: string[]; nearest?: { horizon: string; failed_count: number; as_of: string | null } | null };
  evidence: EvidenceRow[];
  score_as_of: string | null;
  signal_as_of: string | null;
  computed_at: string;
}

/** 결론 이름 — batch/services/verdict.VERDICTS 와 같다 */
export const VERDICT_LABEL: Record<VerdictKey, string> = {
  check_holding: "보유 점검",
  consider_buy: "매수 검토",
  hold: "보유 유지",
  waiting: "신호 대기",
  undecided: "판단 보류",
};

/** 결론 띠 색. 색만으로 뜻을 전하지 않는다 — 이름을 늘 함께 쓴다 */
export const VERDICT_STYLE: Record<VerdictKey, string> = {
  check_holding: "border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200",
  consider_buy: "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  hold: "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-200",
  waiting: "border-slate-300 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200",
  undecided: "border-slate-200 bg-white text-slate-500 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-400",
};

/** 종목 분석 탭의 모아보기 — 사람이 볼 결론만, 이 순서로 */
export const HUB_ORDER: VerdictKey[] = ["check_holding", "consider_buy", "hold"];

/** 모아보기 질의. 결론 하나마다 최대 HUB_LIMIT 줄 */
export const HUB_LIMIT = 30;
export const HUB_SQL = `SELECT v.stock_id, v.market, v.verdict, v.headline, v.computed_at, s.ticker,
  COALESCE(s.name_ko, s.name_en, s.ticker) AS name
FROM stock_verdicts v JOIN stocks s ON s.id = v.stock_id
WHERE v.verdict IN ('check_holding', 'consider_buy', 'hold')
ORDER BY CASE v.verdict WHEN 'check_holding' THEN 0 WHEN 'consider_buy' THEN 1 ELSE 2 END, v.market, s.ticker
LIMIT 90`;

export interface HubRow {
  stock_id: number;
  market: string;
  verdict: VerdictKey;
  headline: string;
  computed_at: string;
  ticker: string;
  name: string;
}

/** 모아보기 묶음 — 결론별로 HUB_LIMIT 까지. 비어 있는 결론은 뺀다 */
export function groupHub(rows: HubRow[]): Array<{ key: VerdictKey; label: string; rows: HubRow[] }> {
  return HUB_ORDER.map((key) => ({ key, label: VERDICT_LABEL[key], rows: rows.filter((r) => r.verdict === key).slice(0, HUB_LIMIT) }))
    .filter((g) => g.rows.length > 0);
}
