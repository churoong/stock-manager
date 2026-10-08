/**
 * 종목 분석 (docs/analysis.md, docs/infra.md 25.1016). 의견은 일일 배치가 만든다 — 여기서는 모양과 글자만 정한다.
 */

import type { DispatchJob, DispatchResult } from "@/lib/dispatch";

export type VerdictKey = "check_holding" | "consider_buy" | "hold" | "reference" | "waiting" | "undecided";

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
  detail: {
    label?: string; reasons?: string[]; against?: string[]; nearest?: { horizon: string; failed_count: number; as_of: string | null } | null;
    /** 참고 분석만 (25.1019) */
    excluded_reason?: string; reference_score?: ReferenceScoreData | null;
    /** 가격·가치 진단 — 예측이 아니라 근거 있는 기준점 (docs/analysis.md 9장, 25.1023) */
    outlook?: OutlookData | null;
    /** 예측 성적표 — 쌓은 예측을 기간이 지나 실제와 견준 누계 (docs/analysis.md 11장, 25.1037) */
    track?: TrackData | null;
    /** 4주 전보다 점수 변화 (docs/analysis.md 17장, 25.1041) */
    score_change?: { since: string; delta: number; factors: Record<string, number>; up?: string; down?: string } | null;
    /** 팩터 모양이 닮은 종목 (docs/analysis.md 18장, 25.1041) */
    twins?: Twin[];
  };
  evidence: EvidenceRow[];
  score_as_of: string | null;
  signal_as_of: string | null;
  computed_at: string;
}

/** 가격·가치 진단 — 배치(`verdict.outlook`)가 만든 문장과 값. 화면은 그리기만 한다 */
export interface OutlookData {
  lines: string[];
  close: number | null;
  close_date: string | null;
  band?: { rank: number | null; pbr: number; prices: { p20: number | null; p50: number | null; p80: number | null }; price_date: string | null };
  consensus?: { brokers: number; median: number; low: number; high: number; upside: number | null; latest: string };
  /** 예상 주가 — CAPM + 변동성 범위 (docs/analysis.md 10장, 25.1024·25.1025) */
  forecast?: ForecastData | null;
  /** 가격 사다리 (docs/analysis.md 12.2·12.3, 25.1038) */
  ladder?: LadderData | null;
  /** 비슷한 국면 — 자기 과거 (docs/analysis.md 13장, 25.1039) */
  analog?: AnalogData | null;
  /** 1년 시나리오 — 순자산 성장 × PBR 밴드 (docs/analysis.md 14장, 25.1040) */
  scenario?: ScenarioData | null;
  /** 역DCF·수급 흐름·네 눈 (docs/analysis.md 15·16·19장, 25.1041) */
  reverse_dcf?: { implied_growth: number; ep: number; discount: number; per: number } | null;
  flows?: FlowCard | null;
  agreement?: Agreement | null;
}

export interface FlowCard {
  latest: string;
  windows: Record<string, { days: number; frgn: number; orgn: number; prsn: number }>;
  frgn_streak: number;
  short?: { recent: number; before: number };
  credit?: { now: number; before: number };
  lines: string[];
}

export interface Agreement {
  views: Record<string, number>;
  up: number;
  n: number;
  spread: number;
}

export interface Twin {
  stock_id: number;
  ticker: string;
  name: string;
  total: number | null;
  dist: number;
  signal: boolean;
}

/** 네 눈을 수직선에 놓을 자리 (0~100%). 0% 수익 선도 함께 — 표시용 비례 계산이다 */
export function agreementPositions(a: Agreement): { zero: number; marks: Array<{ model: string; value: number; pos: number }> } {
  const vals = Object.values(a.views);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const pos = (v: number) => ((v - lo) / span) * 100;
  return { zero: pos(0), marks: Object.entries(a.views).map(([model, value]) => ({ model, value, pos: pos(value) })) };
}

export interface AnalogDist {
  n: number;
  median: number;
  p05: number;
  p16: number;
  p84: number;
  p95: number;
  up: number;
}

export interface AnalogData {
  key: string;
  label: string;
  days?: number;
  episodes?: number;
  since?: string;
  until?: string;
  empty?: boolean;
  horizons?: Array<AnalogDist & { months: number; base?: AnalogDist | null }>;
}

export interface ScenarioData {
  growth: number;
  roe: number;
  payout_part: number;
  bear: number;
  base: number;
  bull: number;
  hold: number;
  as_of?: string | null;
}

export interface LadderItem {
  label: string;
  price: number;
  kind: "high" | "band" | "consensus" | "range" | "signal" | "target" | "stop" | "criterion";
  source: string;
  /** 지금 종가에서의 거리 (소수) */
  dist: number;
  /** 개월 → 그 안에 한 번이라도 닿을 확률 */
  touch?: Record<string, number>;
  need?: "above" | "below";
  met?: boolean;
}

export interface LadderData {
  close: number;
  items: LadderItem[];
  touch_months: number[];
  /** 도달 확률을 냈나(변동성·기대수익이 있을 때만) */
  assumed: boolean;
  race?: { target: number; stop: number; p: number };
}

/** 사다리에 "지금" 줄을 끼운 순서 — 가격 내림차순, 지금 종가는 그 자리에 */
export function ladderWithNow(l: LadderData): Array<LadderItem | { now: true; price: number }> {
  const out: Array<LadderItem | { now: true; price: number }> = [];
  let placed = false;
  for (const it of l.items) {
    if (!placed && it.price <= l.close) {
      out.push({ now: true, price: l.close });
      placed = true;
    }
    out.push(it);
  }
  if (!placed) out.push({ now: true, price: l.close });
  return out;
}

export interface ForecastHorizon {
  months: number;
  expected: number;
  low68?: number;
  high68?: number;
  low90?: number;
  high90?: number;
  /** 그 기간 뒤 지금보다 높을 확률·−20% 이하일 확률 (docs/analysis.md 12.1, 25.1038) */
  p_up?: number;
  p_drop?: number;
}

export interface ForecastData {
  horizons: ForecastHorizon[];
  er: number;
  beta: number;
  beta_given: boolean;
  sigma: number | null;
  rf: number;
  rf_given: boolean;
  market: { annual: number; years: number; since: string; until: string; index?: string };
}

/** 예상 주가 표의 기간 이름 */
export function horizonLabel(months: number): string {
  return months === 12 ? "1년" : `${months}개월`;
}

/** 진단 줄 가운데 표로 따로 그리는 줄(예상 주가)은 목록에서 뺀다 — 같은 숫자를 두 번 보이지 않는다 */
export function outlookListLines(lines: string[]): string[] {
  // 비슷한 국면·시나리오도 맨 위 예측 묶음(ForecastModels)에 표로 있다 (25.1039·25.1040)
  return lines.filter((l) => !l.startsWith("예상 주가:") && !l.startsWith("비슷한 국면(") && !l.startsWith("1년 시나리오("));
}

/** 유니버스 밖 종목의 참고 점수 — `analyze_extra` 가 detail_json 에 둔다 (25.1019) */
export interface ReferenceScoreData {
  total: number;
  rank: number | null;
  ranked: number | null;
  as_of: string;
  factors: Record<string, number | null>;
}

/** 결론 이름 — batch/services/verdict.VERDICTS 와 같다 */
export const VERDICT_LABEL: Record<VerdictKey, string> = {
  check_holding: "보유 점검",
  consider_buy: "매수 검토",
  hold: "보유 유지",
  reference: "참고 분석",
  // 예전 이름 "신호 대기" (25.1024 사용자 지시)
  waiting: "종합 분석",
  undecided: "판단 보류",
};

/** 결론 띠 색. 색만으로 뜻을 전하지 않는다 — 이름을 늘 함께 쓴다 */
export const VERDICT_STYLE: Record<VerdictKey, string> = {
  check_holding: "border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200",
  consider_buy: "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  hold: "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-200",
  reference: "border-violet-300 bg-violet-50 text-violet-800 dark:border-violet-900 dark:bg-violet-950 dark:text-violet-200",
  waiting: "border-slate-300 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200",
  undecided: "border-slate-200 bg-white text-slate-500 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-400",
};

/** 종목 분석 탭의 모아보기 — 사람이 볼 결론만, 이 순서로 */
export const HUB_ORDER: VerdictKey[] = ["check_holding", "consider_buy", "hold", "reference"];

/** 모아보기 질의. 결론 하나마다 최대 HUB_LIMIT 줄 */
export const HUB_LIMIT = 30;
export const HUB_SQL = `SELECT v.stock_id, v.market, v.verdict, v.headline, v.computed_at, s.ticker,
  COALESCE(s.name_ko, s.name_en, s.ticker) AS name
FROM stock_verdicts v JOIN stocks s ON s.id = v.stock_id
WHERE v.verdict IN ('check_holding', 'consider_buy', 'hold', 'reference')
ORDER BY CASE v.verdict WHEN 'check_holding' THEN 0 WHEN 'consider_buy' THEN 1 WHEN 'hold' THEN 2 ELSE 3 END, v.market, s.ticker
LIMIT 120`;

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

// ---------------------------------------------------------------------------
// 지금 분석 (docs/analysis.md 8장, docs/infra.md 25.1018)
// ---------------------------------------------------------------------------

/** `analyze-stock.yml` 의 `repository_dispatch.types` */
export const ANALYZE_EVENT = "analyze-stock";
/**
 * 같은 종목을 다시 눌러도 새로 깨우지 않는 시간. 러너가 뜨고(1~2분) 재무·지표를 받고 유니버스 전 종목으로 점수를 다시 내는 데
 * 몇 분이 걸린다 — 수동 실행 잠금(`RUN_REQUEST_LOCK_MINUTES`)과 같은 15분 [확인필요: 실측]. 그보다 오래 '요청됨' 이면 멈춘 것으로 보고 다시 깨운다
 */
export const REQUEST_LOCK_MINUTES = 15;

export const REQUEST_STOCK_SQL = "SELECT asset_type FROM stocks WHERE id = ?";
/** 관심 종목에 넣는다 — 이미 있으면 목표가·메모·알림 설정을 건드리지 않는다 */
export const REQUEST_WATCH_SQL = `INSERT INTO watchlist (stock_id, added_at, alert_enabled) VALUES (?, ?, 1)
ON CONFLICT (stock_id) DO NOTHING`;
export const REQUEST_GET_SQL = "SELECT status, requested_at, finished_at, note FROM analysis_requests WHERE stock_id = ?";
export const REQUEST_UPSERT_SQL = `INSERT INTO analysis_requests (stock_id, requested_at, status, finished_at, note) VALUES (?, ?, 'requested', NULL, NULL)
ON CONFLICT (stock_id) DO UPDATE SET requested_at = excluded.requested_at, status = 'requested', finished_at = NULL, note = NULL`;
export const REQUEST_FAIL_SQL = "UPDATE analysis_requests SET status = 'failed', finished_at = ?, note = ? WHERE stock_id = ?";

export interface AnalysisRequest {
  status: "requested" | "running" | "done" | "failed";
  requested_at: string;
  finished_at: string | null;
  note: string | null;
}

/** 아직 도는 요청인가 — 요청됨·도는 중이고 잠금 시간 안 */
export function requestInFlight(r: AnalysisRequest | null | undefined, now: Date): boolean {
  if (!r || (r.status !== "requested" && r.status !== "running")) return false;
  const t = Date.parse(r.requested_at);
  return Number.isFinite(t) && now.getTime() - t < REQUEST_LOCK_MINUTES * 60_000;
}

/** 이 종목 하나를 깨우는 작업. 종목 번호는 글자로 넘기고 워크플로가 정수로만 받는다 */
export function analyzeJob(stockId: number): DispatchJob {
  return { key: "analyze_stock", label: "종목 참고 분석", event: ANALYZE_EVENT, payload: { stock_id: String(stockId) }, note: "" };
}

type Exec = (sql: string, args?: Array<string | number | null>) => Promise<Array<Record<string, unknown>>>;

export type RequestOutcome =
  | { ok: true; state: "dispatched" | "in_flight" }
  | { ok: false; status: number; error: string };

/**
 * "지금 분석" — 관심 종목에 넣고, 요청을 적고, 작업을 깨운다. 깨우지 못하면 요청을 실패로 닫는다
 * (관심 종목에는 남는다 — 다음 일일 배치가 참고 분석을 만든다).
 */
export async function requestAnalysis(
  exec: Exec, stockId: number, now: Date, dispatch: (job: DispatchJob) => Promise<DispatchResult>,
): Promise<RequestOutcome> {
  const stock = (await exec(REQUEST_STOCK_SQL, [stockId]))[0];
  if (!stock) return { ok: false, status: 400, error: "그런 종목이 없습니다" };
  if (stock.asset_type !== "stock") return { ok: false, status: 400, error: "ETF 는 종목 분석 대상이 아닙니다(장기 적립 탭에서 봅니다)" };
  const stamp = now.toISOString();
  await exec(REQUEST_WATCH_SQL, [stockId, stamp]);
  const prev = (await exec(REQUEST_GET_SQL, [stockId]))[0] as unknown as AnalysisRequest | undefined;
  if (requestInFlight(prev, now)) return { ok: true, state: "in_flight" };
  await exec(REQUEST_UPSERT_SQL, [stockId, stamp]);
  const res = await dispatch(analyzeJob(stockId));
  if (!res.dispatched) {
    const reason = res.reason ?? "작업을 깨우지 못했습니다";
    await exec(REQUEST_FAIL_SQL, [stamp, reason, stockId]);
    return { ok: false, status: 502, error: `${reason} — 관심 종목에는 넣었습니다. 다음 일일 배치가 참고 분석을 만듭니다` };
  }
  return { ok: true, state: "dispatched" };
}

// ---------------------------------------------------------------------------
// 예측 성적표 (docs/analysis.md 11장, docs/infra.md 25.1037)
// ---------------------------------------------------------------------------

/** 누계 한 칸의 자리 — batch/services/forecast_track.FIELDS 와 같은 순서 */
export const TRACK_FIELDS = ["n", "n_range", "in68", "in90", "dir_n", "dir_hit", "abs_err_sum"] as const;
/** 모델 이름 — forecast_track.MODELS 와 같다 */
export const MODEL_LABEL: Record<string, string> = {
  capm: "CAPM(시장·베타)", consensus: "증권사 목표가", analog: "비슷한 국면", scenario: "시나리오(밴드×순자산)",
};
/** 비율을 말할 최소 표본 — forecast_track.MIN_SAMPLE 과 같다 */
export const TRACK_MIN_SAMPLE = 20;

export type TrackCells = Record<string, Record<string, number[]>>;
export interface TrackData {
  stock?: TrackCells;
  market?: TrackCells;
  since?: string | null;
  first_due?: string | null;
}

export interface TrackRow {
  model: string;
  months: number;
  n: number;
  in68: number | null;
  dir: number | null;
  err: number | null;
  enough: boolean;
}

/** 누계 → 표 줄 (모델·기간 순). 비율은 표시용 나눗셈이다 — 배치가 센 수를 그대로 나눈다 */
export function trackRows(cells: TrackCells | undefined): TrackRow[] {
  const out: TrackRow[] = [];
  for (const model of Object.keys(MODEL_LABEL)) {
    const m = cells?.[model];
    if (!m) continue;
    for (const months of Object.keys(m).map(Number).sort((a, b) => a - b)) {
      const [n, nr, i68, , dn, dh, err] = m[String(months)] ?? [];
      if (!n) continue;
      out.push({ model, months, n, in68: nr ? i68 / nr : null, dir: dn ? dh / dn : null, err: err / n, enough: n >= TRACK_MIN_SAMPLE });
    }
  }
  return out;
}
