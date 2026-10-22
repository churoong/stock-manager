/**
 * 운용 상황실(첫 화면) — 배치가 적은 그날 요약을 읽어 보인다 (docs/desk.md 2장, docs/infra.md 25.1501).
 *
 * 계좌 순위·공통 구간·R 대비·확인할 일은 배치(`batch/services/desk.py`)가 낸 값 그대로다 — 웹은 계산하지 않는다.
 * 여기서 하는 일은 고르기·글 만들기뿐: 요약은 하루 한 줄이고 국내 배치가 아침에 새 줄을 열므로, 그날 밤 미국 배치 전까지는
 * 새 줄에 미국 칸이 없다. 그래서 두 줄을 읽어 **시장마다 가장 최근 칸**을 고른다(각 칸의 실행 시각을 함께 보인다).
 */

import { apartDigits } from "@/lib/apart";
import { amountText, statusText } from "@/lib/kisPaper";

/** 최근 두 날 — 시장마다 가장 최근 칸을 고르려고. 기본키 역순이라 두 행만 읽는다 */
export const DESK_LATEST = "SELECT as_of, summary_json, updated_at FROM paper_desk ORDER BY as_of DESC LIMIT 2";

export type DeskMarketKey = "KR" | "US";
export interface DeskRow {
  id: string; name: string; kind: string; open: string; last: string;
  since_open_pct: number; window_pct: number | null; vs_r_pp: number | null;
  /** 갓 연 계좌 — 제 개설일부터 잰 수익률·같은 구간 R (25.1503). 그 전 요약에는 없다 */
  young?: boolean;
}
export interface DeskLeaderboard { window_start: string | null; control_pct?: number | null; rows: DeskRow[] }
/** 판 이력 한 줄 (docs/desk.md 3장) — 그 판 첫 결정일 종가부터 다음 판 첫 결정일 종가까지(to 가 null 이면 지금 판, from '' 이면 계좌의 처음부터) */
export interface RuleSpan {
  version: number; from: string; to: string | null; orders: number | null;
  pct: number | null; control_pct: number | null; vs_r_pp: number | null;
  /** 판 사전 등록(25.1520) — null = 등록 없이 연 판, 없음 = 그 전 요약 */
  prereg?: { hypothesis: string; metric: string; retro?: boolean } | null;
}
export interface DeskCheck { level: "high" | "normal"; text: string }
export interface DeskFill { side: string; name: string; ticker: string; quantity: number; price: number; currency: string; fill_date?: string }
export interface DeskOrder {
  side: string; name: string; ticker: string; quantity: number; order_type: string; limit_price: number | null;
  tranche: number | null; currency: string; reason: string; fill_session?: string;
}
export interface DeskKisOrder {
  side: string; name: string; quantity: number; order_type: string; limit_price: number | null; status: string;
  reason: string | null; message: string;
}
export interface DeskMarket {
  run_at: string; trade_date: string; session_date: string;
  fills_h: DeskFill[]; fills_by_account: Record<string, number>;
  orders_h: DeskOrder[]; orders_by_account: Record<string, number>;
  kis_orders: DeskKisOrder[]; kis_step: Record<string, unknown>; checks: DeskCheck[];
}
export interface DeskKisAccount {
  as_of: string | null;
  account_krw: { total_krw: number | null } | null;
  perf: import("@/lib/kisPaper").KisPerf | null;
}
/** 실전 전환 1단계 관문 (docs/desk.md 6장, 25.1508) — 읽기 전용. 넘어도 아무것도 열리지 않는다 */
export interface GateItem { key: string; label: string; now: number | null; target: number | null; ok: boolean; n?: number; mean?: number | null; index_pct?: number | null; index_used?: string[]; detail?: Record<string, number>; path_pct?: number | null; path_n?: number | null; exposure_matched?: ExposureMatched | null }
/** 관문 R 대비 옆 참고 칸 — 노출을 H 에 맞춘 R (25.1702). 통과 판정에 들지 않는다 */
export interface ExposureMatched { vs_r?: number; r_pct: number; h_exposure_pct: number; r_exposure_pct: number; skipped_days: number }
export interface Gate { stage: number; account: string; version?: number | null; from?: string | null; items: GateItem[]; passed: boolean; next: string }

/**
 * 화면 글에 쓰는 문턱 — `batch/services/desk.py` 의 상수와 같은 값(테스트가 대조, 25.1511 검토 8).
 * 상수를 바꾸면 화면 글만 낡지 않게
 */
export const DESK_RULES = {
  RISK_NEAR_STOP_PP: 2, CHAMPION_BLOCK: 20, CHAMPION_MIN_BLOCKS: 3, CHAMPION_WIN_SHARE: 0.67,
  GATE_SEL_MIN_BLOCKS: 6, CHAMPION_EVIDENCE_MIN_BLOCKS: 4, CHAMPION_ALPHA: 0.05,
} as const;
export interface RiskRow {
  id: string; exposure_pct: number | null; positions: number; top_weight_pct: number | null; top_name: string | null;
  near_stop: Array<{ name: string | null; unrealized_pct: number; stop_pct: number }>; dd_pct: number | null;
}
/** 실전 준비 목록 한 항목 (docs/paper.md 14장, 25.1513) — 상태는 배치 코드의 표, 근거는 기록에서 */
export interface ReadinessItem { key: string; label: string; status: "done" | "partial" | "rehearsal" | "todo" | "user"; note: string; evidence: string | null }
export const READINESS_TEXT: Record<ReadinessItem["status"], string> = {
  done: "갖춤",
  partial: "일부",
  rehearsal: "모의로 연습 중",
  todo: "아직",
  user: "사용자 결정·확인",
};
/** 25.1518 — `blocks_ok` = 블록 규칙, `candidate` = 블록 규칙 + 결정 단위 짝 차이 하한 > 0. 그 전 요약에는 앞의 둘이 없다 */
export interface ChampionRow {
  id: string; from: string; blocks: number; wins: number; pct: number | null; champion_pct: number | null; candidate: boolean;
  blocks_ok?: boolean; evidence_diff?: { n_blocks: number | null; mean?: number | null; lower?: number | null; alpha: number };
}

/**
 * KIS모의 체결 기준 보정(근사·참고, docs/desk.md 8장, 25.1514) — 배치가 낸 값. 순위는 바꾸지 않는다.
 * adj = 회전 × KIS 가 잰 "장부 가정 넘는 비용"(양수면 덜 벌었다) · pi = 지정가 매수가 시가로 더 싸게 찼을 몫 · band = 칸 값 불확실성
 */
export interface FillAdjustRow {
  from: string; fills: number; turnover_pct: number; adj_pp: number; band_pp: number; unmeasured_pct: number;
  pi_pp: number; pi_missing: number; limit_buy_pct: number | null; adjusted_pct?: number | null; close_call?: boolean;
  /** 잰 칸의 회전(%) — 25.1516 전 요약에는 없다 */
  measured_pct?: number;
}
export interface FillAdjustCheck {
  from: string | null; to: string | null; actual_pp: number; explained_pp: number; band_pp: number; unmeasured_pct: number;
  /** 시작일에 H 가 들고 KIS 에 없던 종목 수 — 있으면 실측이 흔들려 판정에서 뺀다(25.1516) */
  pre_link?: number;
}
export interface FillAdjust { rows: Record<string, FillAdjustRow>; h_check: FillAdjustCheck | null; min_days: number }

export interface DeskSummary {
  fill_adjust?: FillAdjust | null; fill_adjust_error?: string | null;
  leaderboard: DeskLeaderboard; rule_history?: Record<string, RuleSpan[]>; mirror_account: string; updated_at: string;
  gate?: Gate | null; risk?: RiskRow[]; champion?: ChampionRow[]; gate_error?: string | null; readiness?: ReadinessItem[];
  last_market: DeskMarketKey;
  kis_account: DeskKisAccount; markets: Partial<Record<DeskMarketKey, DeskMarket>>;
}
/** 화면에 넘기는 꼴 — 순위·KIS 는 가장 최근 줄, 시장 칸은 시장마다 가장 최근 줄 것 */
export interface DeskView {
  as_of: string | null;
  updated_at: string | null;
  leaderboard: DeskLeaderboard | null;
  /** 25.1502 전 요약에는 없다 */
  rule_history: Record<string, RuleSpan[]>;
  /** 25.1508 전 요약에는 없다 */
  gate: Gate | null;
  risk: RiskRow[];
  champion: ChampionRow[];
  /** 관문·위험판·챔피언 계산이 실패한 실행이면 그 까닭 (25.1511) */
  gate_error: string | null;
  /** 25.1513 전 요약에는 없다 */
  readiness: ReadinessItem[];
  /** 25.1514 전 요약에는 없다 */
  fill_adjust: FillAdjust | null;
  fill_adjust_error: string | null;
  mirror_account: string | null;
  kis_account: DeskKisAccount | null;
  markets: Partial<Record<DeskMarketKey, DeskMarket & { as_of: string }>>;
}

export const EMPTY_DESK: DeskView = {
  as_of: null, updated_at: null, leaderboard: null, rule_history: {}, gate: null, risk: [], champion: [], gate_error: null, readiness: [], fill_adjust: null, fill_adjust_error: null, mirror_account: null, kis_account: null, markets: {},
};

/** 최근 줄들(날짜 역순)에서 보일 것을 고른다. 깨진 JSON 줄은 없는 셈 친다 */
export function pickDesk(rows: Array<{ as_of: string; summary_json: string | null; updated_at: string | null }>): DeskView {
  const 읽은 = rows.flatMap((r) => {
    try {
      const s = JSON.parse(r.summary_json || "null") as DeskSummary | null;
      return s && typeof s === "object" ? [{ as_of: r.as_of, updated_at: r.updated_at, s }] : [];
    } catch {
      return [];
    }
  });
  if (!읽은.length) return EMPTY_DESK;
  const 첫 = 읽은[0];
  const markets: DeskView["markets"] = {};
  for (const m of ["KR", "US"] as const) {
    const 줄 = 읽은.find((x) => x.s.markets?.[m]);
    if (줄) markets[m] = { ...(줄.s.markets[m] as DeskMarket), as_of: 줄.as_of };
  }
  return {
    as_of: 첫.as_of,
    updated_at: 첫.updated_at,
    leaderboard: 첫.s.leaderboard ?? null,
    rule_history: 첫.s.rule_history ?? {},
    gate: 첫.s.gate ?? null,
    risk: Array.isArray(첫.s.risk) ? 첫.s.risk : [],
    champion: Array.isArray(첫.s.champion) ? 첫.s.champion : [],
    gate_error: 첫.s.gate_error ?? null,
    readiness: Array.isArray(첫.s.readiness) ? 첫.s.readiness : [],
    fill_adjust: 첫.s.fill_adjust && typeof 첫.s.fill_adjust === "object" && 첫.s.fill_adjust.rows ? 첫.s.fill_adjust : null,
    fill_adjust_error: 첫.s.fill_adjust_error ?? null,
    mirror_account: 첫.s.mirror_account ?? null,
    kis_account: 첫.s.kis_account ?? null,
    markets,
  };
}

export const MARKET_LABEL: Record<DeskMarketKey, string> = { KR: "국내", US: "미국" };

/** UTC 시각 → 한국 날짜(YYYY-MM-DD). 요약 날짜와 견줄 때 */
export function kstDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = new Date(iso);
  return Number.isNaN(t.getTime()) ? null : t.toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
}

/**
 * 확인할 일 — 시장 칸마다 따로 적힌 것을 합친다. 높음이 먼저, 같은 글은 한 번(멈춤 스위치처럼 두 시장에 다 나오는 것).
 * 그 칸이 요약 날짜보다 앞서 돈 실행이면(이어받은 칸 — 미국 휴장·밤 미국 배치 전) `ran` 에 그 실행일 (25.1503)
 */
export function allChecks(markets: DeskView["markets"], asOf?: string | null): Array<DeskCheck & { market: DeskMarketKey; ran: string | null }> {
  const 본 = new Set<string>();
  const out: Array<DeskCheck & { market: DeskMarketKey; ran: string | null }> = [];
  for (const m of ["KR", "US"] as const) {
    const 칸 = markets[m];
    const 날 = kstDate(칸?.run_at);
    for (const c of 칸?.checks ?? []) {
      if (본.has(c.text)) continue;
      본.add(c.text);
      out.push({ ...c, market: m, ran: 날 && asOf && 날 !== asOf ? 날 : null });
    }
  }
  return out.sort((a, b) => Number(a.level !== "high") - Number(b.level !== "high"));
}

export function pctText(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "-";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function ppText(v: number | null | undefined, threshold?: number): string {
  if (v == null || !Number.isFinite(v)) return "-";
  return `${v >= 0 ? "+" : ""}${v.toFixed(apartDigits(v, threshold, 2))}%p`;
}

const SIDE: Record<string, string> = { buy: "매수", sell: "매도" };
const cur = (c: string): "KRW" | "USD" => (c === "USD" ? "USD" : "KRW");

export function fillLine(f: DeskFill): string {
  return `${SIDE[f.side] ?? f.side} ${f.name} ${f.quantity.toLocaleString("ko-KR")}주 @ ${amountText(f.price, cur(f.currency))}`;
}

export function orderLine(o: DeskOrder): string {
  const 값 = o.order_type === "market" || o.limit_price == null ? "시장가" : `지정가 ${amountText(o.limit_price, cur(o.currency))}`;
  const 차례 = o.tranche && o.tranche > 1 ? ` · ${o.tranche}차` : "";
  return `${SIDE[o.side] ?? o.side} ${o.name} ${o.quantity.toLocaleString("ko-KR")}주 · ${값}${차례}`;
}

/** {계좌: 건수} → "A 2 · C 1" (계좌 이름순) */
export function countsText(c: Record<string, number> | null | undefined): string {
  const 줄 = Object.entries(c ?? {}).filter(([, n]) => n > 0).sort(([a], [b]) => a.localeCompare(b));
  return 줄.length ? 줄.map(([a, n]) => `${a} ${n}`).join(" · ") : "없음";
}

/** KIS모의투자에 낸 주문의 상태별 건수 — "접수 3 · 거부 1" */
export function kisStatusText(orders: DeskKisOrder[]): string {
  const 셈 = new Map<string, number>();
  for (const o of orders) 셈.set(statusText(o.status), (셈.get(statusText(o.status)) ?? 0) + 1);
  return 셈.size ? [...셈].map(([s, n]) => `${s} ${n}`).join(" · ") : "낸 주문 없음";
}

/** KIS 단계가 꺼져 있었거나 보내지 않았으면 그 까닭(키 없음·장 끝난 뒤·접수 시작 전 등). 보냈으면 null (25.1503, 검토 1) */
export function kisOffText(step: Record<string, unknown> | null | undefined): string | null {
  for (const k of ["off", "send"] as const) {
    const v = step?.[k];
    if (typeof v === "string" && v) return v;
  }
  return null;
}

/** 안 낸 주문 까닭별 건수 — "코드 없음 1 · 멈춤 2". 없으면 null */
export function notSentText(step: Record<string, unknown> | null | undefined): string | null {
  const v = step?.not_sent;
  if (!v || typeof v !== "object") return null;
  const 줄 = Object.entries(v as Record<string, number>).filter(([, n]) => Number(n) > 0);
  return 줄.length ? 줄.map(([k, n]) => `${k} ${n}`).join(" · ") : null;
}

/** 판 이력 한 줄 글 — "판 2 · 2026-10-16~지금 · 주문 34건 · 시간가중 +1.20% (같은 기간 R 평균 +0.40%, R 대비 +0.80%p)" */
export function ruleSpanText(r: RuleSpan): string {
  const 끝 = r.to ? `~${r.to}` : "~지금";
  const 대조 = r.control_pct != null ? ` (같은 기간 R 평균 ${pctText(r.control_pct)}${r.vs_r_pp != null ? `, R 대비 ${ppText(r.vs_r_pp)}` : ""})` : "";
  // from '' = 계좌의 처음부터, 주문 수 null = 셀 수 없었음 (25.1505)
  const 주문 = r.orders == null ? "" : ` · 주문 ${r.orders.toLocaleString("ko-KR")}건`;
  return `판 ${r.version} · ${r.from || "처음"}${끝}${주문} · 시간가중 ${pctText(r.pct)}${대조}`;
}

/** 판 가설 한 줄 — "가설: … · 판정: …(사후 등록)" / "사전 등록 없음" / 옛 요약이면 null (25.1520) */
export function preregText(r: RuleSpan): string | null {
  if (r.prereg === undefined) return null;
  if (r.prereg === null) return "사전 등록 없음 — 가설 없이 연 판(성적을 덜 믿는다)";
  return `가설: ${r.prereg.hypothesis} · 판정: ${r.prereg.metric}${r.prereg.retro ? " (장치 전에 연 판 — 나중에 적음)" : ""}`;
}

/** 순위표 줄을 계좌 id 로 — 계좌 비교 표에 공통 구간·R 대비 칸을 붙일 때 */
export function rowsById(lb: DeskLeaderboard | null | undefined): Map<string, DeskRow> {
  return new Map((lb?.rows ?? []).map((r) => [r.id, r]));
}

/** 관문 칸의 지금 값 글 — 칸마다 단위가 다르다 */
export function gateNowText(i: GateItem): string {
  // 결정 단위 — 묶음이 4개 전이라 하한이 없어도 평균·묶음 수는 보인다(25.1523 검토 7)
  if ((i.key === "selection" || i.key === "control") && i.now == null && i.mean != null) return `하한 모름(평균 ${i.mean.toFixed(1)}, 28일 묶음 ${i.n ?? 0}) / ${i.target ?? 50} 초과`;
  // 가상 체결이 모두 경로 실패(모름·거부)면 실현율은 모르지만 경로 포함 값은 있다 — 미국 예약 미전송처럼 이 칸이 가장 필요한 때(25.1627)
  if (i.now == null) return i.key === "kis" && i.path_pct != null ? `모름 · 경로 포함 ${i.path_pct.toFixed(1)}%(표본 ${i.path_n ?? 0}, 참고)` : "모름";
  switch (i.key) {
    case "days":
      return `${i.now}번 / ${i.target}번`;
    case "vs_r": {
      // 노출 맞춘 R 대비(참고, 25.1702) — 하락장엔 현금만으로, 상승장엔 덜 들어가 지는 몫을 뺀 값
      const m = i.exposure_matched;
      const 참고 = m && m.vs_r != null
        ? ` (참고: 노출 맞춘 R 대비 ${ppText(m.vs_r, 0)} — 평균 투자 비중 H ${m.h_exposure_pct.toFixed(0)}% · R ${m.r_exposure_pct.toFixed(0)}%)`
        : "";
      return `${ppText(i.now, 0)} / 0 초과${참고}`;
    }
    case "vs_index":
      // 같은 돈을 KOSPI·S&P500 반반에 넣었다면(25.1519) — 지수 쪽 수익률을 같이
      // 한쪽 그림자가 비면 그쪽은 빠진다 — 어느 지수를 썼는지 적는다(25.1523 검토 2)
      return `${ppText(i.now, 0)} (지수 ${i.index_used?.length === 1 ? `${i.index_used[0]}만` : "반반"} ${pctText(i.index_pct)}) / 0 초과`;
    case "ratio": {
      const d = apartDigits(i.now, i.target, 3);
      return `${i.now.toFixed(d)} / R 평균 ${i.target == null ? "모름" : i.target.toFixed(d)}`;
    }
    case "incidents":
      // 단위는 날 수 — 검산 어긋난 날 + 배치 사고 날(docs/desk.md 6장, 25.1637 — 예전 "건")
      return `${i.now}일(검산 어긋난 날 ${i.detail?.recon_bad_days ?? "-"} · 배치 사고 날 ${i.detail?.batch_failed ?? "-"}) / 0`;
    case "selection":
    case "control":
      // 결정 단위 선정·가상 대조 — 한쪽 95% 하한 분위(무작위면 50), 평균·묶음 수 (25.1518·25.1697)
      return `하한 ${i.now.toFixed(apartDigits(i.now, i.target ?? 50, 1))}분위(평균 ${i.mean == null ? "-" : i.mean.toFixed(1)}, 28일 묶음 ${i.n ?? 0}) / ${i.target ?? 50} 초과`;
    case "kis":
      // 소수 첫째 자리 — 89.6 이 "90% / 90% 이상" 으로 보이는데 미충족이면 헷갈린다(25.1511 검토 4)
      // 경로 포함(모름·거부를 못 찬 것으로, 참고 — 판정 칸 아님, 25.1624)
      return `${i.now.toFixed(apartDigits(i.now, i.target, 1))}% (표본 ${i.n ?? 0}) / ${i.target}% 이상${i.path_pct != null ? ` · 경로 포함 ${i.path_pct.toFixed(1)}%(표본 ${i.path_n ?? 0}, 참고)` : ""}`;
    default:
      return `${i.now} / ${i.target ?? "-"}`;
  }
}

/** 챔피언 교체 후보 한 줄 — "C · 블록 3개 중 3개 이김 · 같은 기간 +12.00% (챔피언 +6.00%) · 2026-01-01부터" */
export function championText(r: ChampionRow, mirror: string): string {
  return `${r.id} · 블록 ${r.blocks}개 중 ${r.wins}개 이김 · 같은 기간 ${pctText(r.pct)} (${mirror} ${pctText(r.champion_pct)}) · ${r.from}부터${evidenceText(r)}`;
}

/** 결정 단위 짝 차이 — " · 결정 단위 +1.20%p(하한 −0.40, 묶음 5)" 또는 " · 결정 단위 근거 부족(묶음 2)". 25.1518 전 요약이면 빈 글 */
export function evidenceText(r: ChampionRow): string {
  const e = r.evidence_diff;
  if (!e) return "";
  if (e.lower == null || e.mean == null) return ` · 결정 단위 근거 부족(묶음 ${e.n_blocks ?? 0})`;
  return ` · 결정 단위 ${ppText(e.mean)}(하한 ${e.lower >= 0 ? "+" : ""}${e.lower.toFixed(2)}, 묶음 ${e.n_blocks})`;
}

/**
 * 보정 칸 글 — "+0.95%" 와 아래 작은 글 "−0.05%p ±0.02 · 개선 +0.01%p · 안 잰 회전 20%".
 * 회전이 없으면 "체결 없음", 잰 칸도 개선도 없으면 보정값 대신 "안 잼"(안 잰 회전이 있으면 그 크기)
 */
export function adjustText(x: FillAdjustRow | undefined | null): { main: string; sub: string } {
  if (!x) return { main: "-", sub: "" };
  if (!x.fills) return { main: x.adjusted_pct == null ? "-" : pctText(x.adjusted_pct), sub: "체결 없음" };
  // 잰 칸이 있었나는 배치 값(`measured_pct`)으로 — 지정가 개선만 있을 때 "±0.00" 으로 잰 것처럼 보였다(25.1516, 검토 5)
  const 잼 = x.measured_pct != null ? x.measured_pct > 0 : x.adj_pp !== 0 || x.band_pp !== 0;
  const 조각 = [
    잼 ? `${x.adj_pp > 0 ? "−" : "+"}${Math.abs(x.adj_pp).toFixed(2)}%p ±${x.band_pp.toFixed(2)}` : "잰 칸 없음",
    x.pi_pp ? `지정가 개선 +${x.pi_pp.toFixed(2)}%p` : "",
    x.unmeasured_pct >= 0.5 ? `안 잰 회전 ${x.unmeasured_pct.toFixed(0)}%` : "",
    x.close_call ? "이웃과 뒤집힐 수 있음" : "",
  ].filter(Boolean);
  return { main: (잼 || x.pi_pp) && x.adjusted_pct != null ? pctText(x.adjusted_pct) : "-", sub: 조각.join(" · ") };
}

/** H 대보기 한 줄 — KIS 실측 손익 차이 vs 칸으로 설명한 몫 */
export function adjustCheckText(c: FillAdjustCheck | null | undefined, mirror: string): string | null {
  if (!c) return null;
  const 섞임 = c.pre_link ? ` · 연결 전 보유 ${c.pre_link}종목이 섞여 실측이 흔들림(판정에서 뺌)` : "";
  return `${c.from ?? "?"}~${c.to ?? "?"} KIS 실측 손익 차이 ${ppText(c.actual_pp)} (${mirror} 평가 대비) · 칸으로 설명한 몫 ${ppText(c.explained_pp)} ±${c.band_pp.toFixed(2)} · 안 잰 회전 ${c.unmeasured_pct.toFixed(0)}%${섞임}`;
}
