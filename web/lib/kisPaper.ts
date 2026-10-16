/**
 * KIS모의투자 웹 보기 — 거래내역·보유내역·성과 (docs/paper.md 15.7, docs/infra.md 25.1496).
 *
 * A: 배치가 적은 기록(`paper_broker_daily`·`paper_broker_orders`)을 읽기만 한다. 수익률·낙폭·H 비교는 배치가 낸 값(`summary_json.perf`),
 * 보유의 현재가·평가금액·손익은 KIS 가 준 값(`holdings_json`)이다 — 웹은 계산하지 않는다.
 * 성과는 **계좌 전체 원화**(국내 배치 줄의 `account_krw`·`perf`) — 계좌 하나에 원·달러가 같이 있고 미국 매수 돈이 원화에서 자동 환전으로
 * 나가 시장별 총액 비는 수익률이 아니다(25.1497).
 */

import { BROKER_KEYS, type BrokerSummary } from "@/lib/paper";

export type KisMarket = keyof typeof BROKER_KEYS;
/** 거래내역·추이를 읽는 기간(달력일) — 배치 비교 창(`COMPARE_DAYS` 28)보다 넉넉히 */
export const KIS_TRADE_DAYS = 90;
export const KIS_TOTALS_DAYS = 400;

export const KIS_LATEST = `SELECT as_of, holdings_json, summary_json, diff_json, fetched_at FROM paper_broker_daily
WHERE broker = ? ORDER BY as_of DESC LIMIT 1`;
/** 계좌 전체 원화 총액 추이 — 배치 `TOTALS_SQL` 과 같은 잣대(국내 배치 줄에만 있다) */
export const KIS_TOTALS = `SELECT as_of, json_extract(summary_json, '$.account_krw.total_krw') AS total
FROM paper_broker_daily WHERE broker = ? AND as_of >= ? ORDER BY as_of`;
export const KIS_ORDERS = `SELECT b.id, b.trade_date, b.side, b.code, b.order_type, b.limit_price, b.quantity, b.status, b.filled_qty,
  b.avg_price, b.reason, b.message, b.sent_at, b.stock_id, COALESCE(s.name_ko, s.name_en, s.ticker) AS name
FROM paper_broker_orders b LEFT JOIN stocks s ON s.id = b.stock_id
WHERE b.broker = ? AND b.trade_date >= ? ORDER BY b.trade_date DESC, b.id DESC`;
export const KIS_NAMES = `SELECT s.id, COALESCE(s.name_ko, s.name_en, s.ticker) AS name FROM json_each(?) j JOIN stocks s ON s.id = j.value`;

export interface KisHolding { id: number | null; code: string; qty: number; avg: number | null; price: number | null; value: number | null; pnl: number | null; name?: string | null }
export interface KisAccount { currency: "KRW" | "USD"; cash: number | null; holdings_value: number | null; total: number | null }
export interface KisPerf {
  start: string; start_total: number; last: string; last_total: number; pnl_krw: number; return_pct: number; mdd_pct: number; days: number;
  h_pnl_krw: number | null; h_return_pct: number | null;
}
export interface KisAccountKrw { kr_total: number | null; value_usd: number | null; cash_usd: number | null; usd_krw: number | null; total_krw: number | null }
export interface KisOrder {
  id: number; trade_date: string; side: string; code: string; order_type: string; limit_price: number | null; quantity: number; status: string;
  filled_qty: number | null; avg_price: number | null; reason: string; message: string | null; sent_at: string | null; stock_id: number; name: string | null;
}
export interface KisMarketView {
  as_of: string | null;
  fetched_at: string | null;
  holdings: KisHolding[];
  account: KisAccount | null;
  summary: BrokerSummary | null;
  diff: Record<string, [number, number]>;
  orders: KisOrder[];
}
/** 계좌 전체(국내 배치 줄) — 원화 총액·성과·추이 */
export interface KisWhole { as_of: string | null; account_krw: KisAccountKrw | null; perf: KisPerf | null; totals: Array<{ as_of: string; total: number | null }>; totals_error?: boolean }

/** 보유 칸 — 지금 꼴은 객체, 25.1496 전 줄은 [id, 수량, 평균가] 배열(현재가·평가 없음) */
export function parseHoldings(json: string | null | undefined): KisHolding[] {
  let raw: unknown;
  try {
    raw = JSON.parse(json || "[]");
  } catch {
    return [];
  }
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((h): KisHolding[] => {
    if (Array.isArray(h)) {
      const [id, qty, avg] = h as [unknown, unknown, unknown];
      return [{ id: typeof id === "number" ? id : null, code: String(id ?? ""), qty: Number(qty) || 0, avg: avg == null ? null : Number(avg), price: null, value: null, pnl: null }];
    }
    if (h && typeof h === "object") {
      const o = h as Record<string, unknown>;
      const n = (v: unknown) => (v == null || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));
      return [{ id: n(o.id), code: String(o.code ?? ""), qty: Number(o.qty) || 0, avg: n(o.avg), price: n(o.price), value: n(o.value), pnl: n(o.pnl) }];
    }
    return [];
  });
}

const STATUS_TEXT: Record<string, string> = {
  sending: "보냄(응답 모름)",
  accepted: "접수",
  filled: "체결",
  partial: "부분 체결",
  unfilled: "미체결",
  rejected: "거부",
  unknown: "모름",
};
export function statusText(s: string): string {
  return STATUS_TEXT[s] ?? s;
}

/** 통화에 맞춘 금액 글 — 원은 정수, 달러는 센트까지 */
export function amountText(v: number | null | undefined, currency: "KRW" | "USD"): string {
  if (v == null || !Number.isFinite(v)) return "-";
  return currency === "USD"
    ? `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : `${Math.round(v).toLocaleString("ko-KR")}원`;
}

/** 지정가 글 — 미국은 시장가가 없어 늘 지정가(시장가 주문은 종가 ± 폭으로 낸 값, docs/paper.md 15.6) */
export function priceText(o: Pick<KisOrder, "order_type" | "limit_price">, currency: "KRW" | "USD"): string {
  return o.order_type === "market" || o.limit_price == null ? "시장가" : amountText(o.limit_price, currency);
}

/** 성과 몇 줄 — 값은 배치가 낸 그대로(계좌 전체 원화) */
export function perfLines(p: KisPerf | null): string[] {
  if (!p) return ["아직 계좌 총액 기록이 없습니다 — 국내 일일 배치가 국내·미국 잔고를 함께 적은 날부터 보입니다."];
  const pct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
  const won = (v: number) => `${v >= 0 ? "+" : "−"}${Math.round(Math.abs(v)).toLocaleString("ko-KR")}원`;
  const out = [
    `${p.start} ${amountText(p.start_total, "KRW")} → ${p.last} ${amountText(p.last_total, "KRW")} · 손익 ${won(p.pnl_krw)} (${pct(p.return_pct)}) · 최대 낙폭 ${p.mdd_pct.toFixed(2)}% (${p.days}일)`,
  ];
  if (p.h_pnl_krw != null)
    out.push(`같은 기간 가상모의투자 H 손익 ${won(p.h_pnl_krw)}${p.h_return_pct != null ? ` (${pct(p.h_return_pct)})` : ""} — 같은 주문을 베끼므로 손익 금액으로 견줍니다(모의 예수금이 H 입금액보다 커 % 는 KIS 쪽이 늘 작습니다)`);
  return out;
}
