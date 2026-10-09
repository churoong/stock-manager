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
    /** 같은 업종 비교 (docs/analysis.md 21장, 25.1045) */
    peers?: Peers | null;
    /** 종합 점수 분해 (docs/analysis.md 26장, 25.1051) */
    decomposition?: Decomposition | null;
    /** 분기 실적 추세 (docs/analysis.md 25장, 25.1049) — 국내 */
    quarters?: { basis: string; trend: "가속" | "감속" | null; rows: unknown[] } | null;
    /** 재무 건전성 — Altman Z″ (docs/analysis.md 38장, 25.1061). 국내만 */
    health?: { fiscal_year: number; consolidated: boolean; report_date: string | null; z: number | null; zone?: "safe" | "grey" | "distress"; parts?: number[]; debt_ratio?: number; note?: string } | null;
    /** 이 종목에 난 신호들의 성적 (docs/analysis.md 37장, 25.1060) */
    signal_history?: SignalHistory | null;
    /** 함께 움직이는 종목 — 시장 몫을 뺀 1년 상관 (52장, 25.1071) */
    comovers?: Array<{ stock_id: number; ticker: string; name: string; corr: number; same_sector: boolean }> | null;
    /** 감성과 가격의 엇갈림 (50장, 25.1070) */
    sentiment_gap?: { as_of: string; sentiment: number | null; sent_delta: number; price_ret: number; opposite: boolean } | null;
    /** 같은 업종 대체 후보 — 점수가 같거나 높고 변동성이 낮은 종목 (47장, 25.1068) */
    alternatives?: Array<{ stock_id: number; ticker: string; name: string; total: number; vol: number; mdd: number | null; my_vol: number; my_total: number }>;
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
  consensus?: {
    brokers: number; median: number; low: number; high: number; upside: number | null; latest: string;
    /** 잘 맞힌 증권사만의 목표가 (docs/analysis.md 27장, 25.1052) */
    skilled?: { n: number; weighted: number; upside: number | null; brokers: Array<{ broker: string; target: number; touch_pct: number; n_touch: number }> } | null;
  };
  /** 모멘텀 원값 (docs/analysis.md 9.1) */
  momentum?: { momentum_3m?: number | null; momentum_6m?: number | null; momentum_12_1?: number | null; high_52w_proximity?: number | null } | null;
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
  /** 목표가 흐름 (docs/analysis.md 36장, 25.1059) — 진단 줄로 보이고 값은 여기 */
  target_trend?: { window: number; raises: number; cuts: number; avg_change: number | null; points: Array<{ days_ago: number; date: string; brokers: number; median: number }> } | null;
  /** 자기 시세 이력의 사실들 — 낙폭 회복·최악의 한 달·계절성·신고가 뒤 (docs/analysis.md 31~34장, 25.1057) */
  history?: HistoryData | null;
}

export interface DistLite { n: number; median: number; up: number; p16?: number; p84?: number }

export interface HistoryData {
  drawdown?: {
    since: string; until: string; now: number; peak_date: string; days_since_peak: number;
    levels: Record<string, { n: number; recovered: number; open: number; median_days: number | null }>;
    worst: Array<{ peak: string; trough: string; depth: number; recovered: string | null; days: number | null }>;
  } | null;
  tail?: { since: string; until: string; d1: { n: number; var: number; cvar: number; worst: number }; m1: { n: number; var: number; cvar: number; worst: number } } | null;
  season?: { since: string; until: string; months: Record<string, { n: number; up: number; avg: number }> } | null;
  breakout?: { events: number; last: string | null; h: Record<string, DistLite | null>; base: Record<string, DistLite | null> } | null;
  market_breakout?: Record<string, DistLite> | null;
  /** 실적 발표 반응 (40장, 국내) */
  earnings?: {
    basis: string; events: number;
    recent: Array<{ date: string; fiscal_year: number; report_code: string; yoy: number | null; r1: number; x1: number | null; r5: number; x5: number | null }>;
    grew: { n: number; avg: number; up: number } | null; shrank: { n: number; avg: number; up: number } | null;
  } | null;
  /** 상승의 출처 (41장, 국내) */
  sources?: { since: string; until: string; from_year: number; to_year: number; price: number; earnings: number; multiple: number; dividends: number | null } | null;
  /** 실제 수익률 경로 (43장) — 화면은 예상 주가 표의 boot 와 사다리의 touch_boot 를 쓴다 */
  boot?: { days_used: number; sigma: number } | null;
  /** 매물대 (45장) — 칸은 마지막 수정주가에 대한 비율 */
  profile?: { since: string; until: string; days: number; bins: Array<{ lo: number; hi: number; share: number }>; top: number[]; now: number } | null;
  /** 변동성 국면 성적 (51장) — 칸 0·1·2 = 조용·보통·시끄러움 */
  vol_regime?: { since: string; until: string; now: number; pct: number; edges: number[]; bucket: number; buckets: Record<string, Record<string, DistLite | null>> } | null;
  /** 시장 시나리오 (46장) — 지수가 1년에 ±20% 넘게 움직인 경로에서 이 종목의 1년 수익(16·50·84%) */
  scenarios?: { days_used: number; beta: number | null; paths: number; move: number; down: { n: number; stock?: number[]; index?: number }; up: { n: number; stock?: number[]; index?: number } } | null;
  /** 움직임 분해 (44장) */
  moves?: {
    until: string; beta: number; w: Record<string, { since: string; stock: number; market: number; sector: number | null; peers: number; own: number }>;
    /** 업종 대형주(시총 상위 30%)의 같은 창 평균 (48장) */
    leaders?: { n: number; of: number; self_leader: boolean; w: Record<string, { leaders: number; stock: number }> } | null;
  } | null;
  /** 공매도 급증 뒤 (42장, 국내) */
  short?: { since: string; until: string; days: number; events: number; last: string | null; h: Record<string, DistLite | null>; base: Record<string, DistLite | null> } | null;
}

/** 진단 줄 가운데 이력 카드(HistoryFacts)가 그리는 줄 — 목록에서 뺀다 */
export const HISTORY_LINE_PREFIXES = ["낙폭 회복(", "최악의 한 달(", "계절성(", "52주 신고가 뒤(", "실적 발표 반응(", "상승의 출처(", "공매도 급증 뒤(", "움직임 분해(", "실제 수익률로 그린 1년 범위(", "시장 시나리오(", "업종 대형주(", "변동성 국면:"];

/** 보고서 코드 → 이름 (DART) */
export const REPORT_NAME: Record<string, string> = { "11013": "1분기", "11012": "반기", "11014": "3분기", "11011": "사업" };

/** 계절성 달력의 칸 색 — 오른 비율(사실)을 그대로 옮긴다. 새 문턱이 아니라 절반 위·아래로 나눌 뿐 */
export function seasonTone(up: number, n: number): "up" | "down" | "even" {
  return up * 2 > n ? "up" : up * 2 < n ? "down" : "even";
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
  /** 성적 가중 합의 (docs/analysis.md 30장) — 시장 전체 1년 성적의 오차 역수로 가중. 성적이 모자라면 value 가 null */
  weighted?: WeightedAgreement | null;
}

export interface WeightedAgreement {
  value: number | null;
  weights?: Record<string, number>;
  left_out?: string[];
  pending?: string[];
  available_from?: string | null;
  n?: Record<string, number>;
}

/** 성적 가중 합의 한 줄 (30장). 배치 값을 글로 옮길 뿐이다 */
export function weightedAgreementLine(w: WeightedAgreement | null | undefined): string | null {
  if (!w) return null;
  const pct = (x: number) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
  if (w.value === null || w.value === undefined) {
    return w.available_from
      ? `성적 가중 합의는 ${w.available_from}부터 냅니다 — 1년 예측의 실제 결과가 쌓여야 어느 눈이 잘 맞히는지 압니다.`
      : "성적 가중 합의는 1년 예측의 실제 결과가 쌓인 뒤에 냅니다.";
  }
  const 무게 = Object.entries(w.weights ?? {}).map(([m, x]) => `${MODEL_LABEL[m] ?? m} ${(x * 100).toFixed(0)}%`).join(" · ");
  const 빠짐 = w.left_out?.length ? ` · 성적이 모자라 뺀 눈: ${w.left_out.map((m) => MODEL_LABEL[m] ?? m).join(", ")}` : "";
  return `성적 가중 1년 예상 ${pct(w.value)} (가중 ${무게}${빠짐})`;
}

export interface Decomposition {
  parts: Array<{ factor: string; score: number; weight: number; contrib: number }>;
  sentiment: number;
  total: number;
  change?: Record<string, number> | null;
  since?: string | null;
}

export interface Peers {
  sector: string;
  n: number;
  ranked: number;
  rank: number | null;
  pbr: number | null;
  pbr_median: number | null;
  roe: number | null;
  roe_median: number | null;
  r3: number | null;
  r3_median: number | null;
  top: Array<{ stock_id: number; ticker: string; name: string; total: number }>;
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
  /** 같은 칸을 오늘 시장 국면(지수 200일선)으로 한 번 더 나눈 분포 (docs/analysis.md 29장) */
  regime?: { state: "bull" | "bear"; label: string; days?: number; empty?: boolean; horizons?: Array<AnalogDist & { months: number }> } | null;
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
  kind: "high" | "band" | "consensus" | "range" | "signal" | "target" | "stop" | "criterion" | "profile";
  source: string;
  /** 지금 종가에서의 거리 (소수) */
  dist: number;
  /** 개월 → 그 안에 한 번이라도 닿을 확률 */
  touch?: Record<string, number>;
  /** 같은 질문을 실제 수익률 경로로(표류 0)와, 같은 변동성의 정규 가정(표류 0) — 꼬리 모양만 견준다 (43장) */
  touch_boot?: Record<string, number | null>;
  touch_norm0?: Record<string, number>;
  need?: "above" | "below";
  met?: boolean;
}

export interface LadderData {
  close: number;
  items: LadderItem[];
  touch_months: number[];
  /** 도달 확률을 냈나(변동성·기대수익이 있을 때만) */
  assumed: boolean;
  /** 목표·손절 경주 (12.3) + 켈리 참고 비중 (35장, 25.1058) — up·down 은 종가 대비 이익·손실 폭(소수) */
  race?: { target: number; stop: number; p: number; up?: number; down?: number; kelly?: number | null };
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
  /** 반반 범위 — 이 안에 들 확률 50% (docs/analysis.md 10.5, 25.1055) */
  low50?: number;
  high50?: number;
  low68?: number;
  high68?: number;
  low90?: number;
  high90?: number;
  /** 그 기간 뒤 지금보다 높을 확률·−20% 이하일 확률 (docs/analysis.md 12.1, 25.1038) */
  p_up?: number;
  p_drop?: number;
  /** 그 기간의 평균 변동성 — 최근 변동성이 장기로 돌아가는 기간 구조 (10.5) */
  sigma?: number;
  /** 실제 수익률로 그린 범위 (43장) — 같은 중심, 꼬리 모양만 다르다 */
  boot?: { low90: number; low68: number; low50: number; median: number; high50: number; high68: number; high90: number };
}

export interface ForecastData {
  horizons: ForecastHorizon[];
  er: number;
  beta: number;
  beta_given: boolean;
  sigma: number | null;
  /** 최근 EWMA 변동성 (10.5). 없으면 장기 변동성 하나로 낸 범위 */
  sigma_short?: number | null;
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
  return lines.filter(
    (l) => !l.startsWith("예상 주가:") && !l.startsWith("비슷한 국면(") && !l.startsWith("1년 시나리오(") && !HISTORY_LINE_PREFIXES.some((p) => l.startsWith(p)),
  );
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

/** 팩터 이름 — batch/services/verdict.FACTOR 와 같다 */
export const FACTOR_NAME: Record<string, string> = { value: "밸류", quality: "퀄리티", growth: "성장", momentum: "모멘텀", risk: "리스크" };

/** 레이더 (docs/analysis.md 20장, 25.1043) — 배치가 시장마다 만들어 설정에 둔다. batch/jobs/verdicts.radar_key 와 같다 */
export const RADAR_KEYS = ["analysis_radar_KR", "analysis_radar_US"] as const;
export const RADAR_SQL = "SELECT key, value FROM settings WHERE key IN ('analysis_radar_KR', 'analysis_radar_US')";

interface RadarHead {
  stock_id: number;
  ticker: string;
  name: string;
  verdict: VerdictKey | null;
}
export interface Radar {
  as_of: string;
  computed_at: string;
  near: Array<RadarHead & { label: string; price: number; dist: number; need: "above" | "below" | null }>;
  rising: Array<RadarHead & { delta: number; up: string | null; since: string | null }>;
  eyes: Array<RadarHead & { low: number; high: number; n: number }>;
  /** 감성과 가격의 엇갈림 (50장, 25.1070) — 예전 기록에는 없다 */
  gap?: Array<RadarHead & { sent_delta: number; price_ret: number }>;
}

/** 설정 행 → 시장별 레이더. 깨진 값은 뺀다 */
export function parseRadars(rows: Array<{ key: string; value: string }>): Record<string, Radar> {
  const out: Record<string, Radar> = {};
  for (const r of rows) {
    try {
      const v = JSON.parse(r.value) as Radar;
      if (v && Array.isArray(v.near) && Array.isArray(v.rising) && Array.isArray(v.eyes)) out[r.key.replace("analysis_radar_", "")] = v;
    } catch {
      /* 깨진 값은 보이지 않는다 — 다음 배치가 덮는다 */
    }
  }
  return out;
}

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

/** 이 종목에 난 신호들의 성적 (docs/analysis.md 37장) — 이어지는 신호는 한 번으로 묶은 뒤 */
export interface SignalHistory {
  signals: number;
  rows: number;
  recent: Array<{ as_of_date: string; horizon: string; ret_5d: number | null; ret_20d: number | null; ret_60d: number | null; hit_target: number | null; hit_stop: number | null; bench_ret_20d: number | null }>;
  n20: number;
  avg20: number | null;
  up20: number;
  excess20: number | null;
  targets: number;
  stops: number;
}

/** 한 줄 요약 칸 (docs/analysis.md 39장) */
export interface SummaryCell { key: "value" | "trend" | "flow" | "earnings" | "risk"; label: string; text: string; tone: "good" | "bad" | "neutral" | "none" }

/**
 * 한 줄 요약 카드 (docs/analysis.md 39장, 25.1062) — 다섯 칸 신호등. **새 문턱을 만들지 않는다**:
 * 가치 = PBR 밴드의 자기 20%·80% 선(9.2 와 같은 선), 추세 = 3개월 수익률의 부호, 수급 = 최근 20거래일 외국인+기관 합의 부호,
 * 실적 = 분기 추세의 가속·감속(25장), 위험 = Altman Z″ 구간(38장, Altman 2000 의 경계). 재료가 없으면 회색 "없음".
 */
export function summaryCells(d: Verdict["detail"] | null | undefined): SummaryCell[] {
  const o = d?.outlook;
  const pct = (x: number) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
  const cells: SummaryCell[] = [];
  const rank = o?.band?.rank;
  cells.push(
    typeof rank === "number"
      ? { key: "value", label: "가치", text: rank <= 20 ? `싼 쪽 (밴드 ${rank.toFixed(0)}%)` : rank >= 80 ? `비싼 쪽 (밴드 ${rank.toFixed(0)}%)` : `중간 (밴드 ${rank.toFixed(0)}%)`, tone: rank <= 20 ? "good" : rank >= 80 ? "bad" : "neutral" }
      : { key: "value", label: "가치", text: "밴드 없음", tone: "none" },
  );
  const m3 = o?.momentum?.momentum_3m;
  cells.push(
    typeof m3 === "number"
      ? { key: "trend", label: "추세", text: `3개월 ${pct(m3)}`, tone: m3 > 0 ? "good" : m3 < 0 ? "bad" : "neutral" }
      : { key: "trend", label: "추세", text: "없음", tone: "none" },
  );
  const w = o?.flows?.windows?.["20"];
  const 합 = w ? w.frgn + w.orgn : null;
  cells.push(
    w && 합 !== null
      ? { key: "flow", label: "수급", text: `외국인+기관 ${합 >= 0 ? "+" : ""}${합.toLocaleString(undefined, { maximumFractionDigits: 0 })}억 (${w.days}일)`, tone: 합 > 0 ? "good" : 합 < 0 ? "bad" : "neutral" }
      : { key: "flow", label: "수급", text: "없음(국내만)", tone: "none" },
  );
  const q = d?.quarters;
  cells.push(
    q
      ? { key: "earnings", label: "실적", text: q.trend ? `영업이익 증가율 ${q.trend}` : "뚜렷한 방향 없음", tone: q.trend === "가속" ? "good" : q.trend === "감속" ? "bad" : "neutral" }
      : { key: "earnings", label: "실적", text: "없음(국내만)", tone: "none" },
  );
  const h = d?.health;
  cells.push(
    h && typeof h.z === "number" && h.zone
      ? { key: "risk", label: "재무 위험", text: `Z″ ${h.z.toFixed(2)} ${h.zone === "safe" ? "안전" : h.zone === "distress" ? "위험" : "회색"}`, tone: h.zone === "safe" ? "good" : h.zone === "distress" ? "bad" : "neutral" }
      : { key: "risk", label: "재무 위험", text: h?.note ? "해당 없음" : "없음", tone: "none" },
  );
  return cells;
}

