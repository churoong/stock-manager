/**
 * 운용 상황실(첫 화면) — 배치가 적은 그날 요약을 읽어 보인다 (docs/desk.md 2장, docs/infra.md 25.1501).
 *
 * 계좌 순위·공통 구간·R 대비·확인할 일은 배치(`batch/services/desk.py`)가 낸 값 그대로다 — 웹은 계산하지 않는다.
 * 여기서 하는 일은 고르기·글 만들기뿐: 요약은 하루 한 줄이고 국내 배치가 아침에 새 줄을 열므로, 그날 밤 미국 배치 전까지는
 * 새 줄에 미국 칸이 없다. 그래서 두 줄을 읽어 **시장마다 가장 최근 칸**을 고른다(각 칸의 실행 시각을 함께 보인다).
 */

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
export interface DeskSummary {
  leaderboard: DeskLeaderboard; rule_history?: Record<string, RuleSpan[]>; mirror_account: string; updated_at: string;
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
  mirror_account: string | null;
  kis_account: DeskKisAccount | null;
  markets: Partial<Record<DeskMarketKey, DeskMarket & { as_of: string }>>;
}

export const EMPTY_DESK: DeskView = {
  as_of: null, updated_at: null, leaderboard: null, rule_history: {}, mirror_account: null, kis_account: null, markets: {},
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

export function ppText(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "-";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%p`;
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

/** 순위표 줄을 계좌 id 로 — 계좌 비교 표에 공통 구간·R 대비 칸을 붙일 때 */
export function rowsById(lb: DeskLeaderboard | null | undefined): Map<string, DeskRow> {
  return new Map((lb?.rows ?? []).map((r) => [r.id, r]));
}
