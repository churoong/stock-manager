/**
 * 연구실 — 전략을 고치려고 보는 자료 (docs/desk.md 5장, docs/infra.md 25.1506).
 *
 * 성과 분해 비교(계좌마다 배치가 낸 `paper_daily.attribution_json` 이 **적힌** 마지막 것 — 25.1630, /paper 의 25.1602 와 같게. 한 번도 안 적힌 계좌는 마지막 평가 행 그대로 '표본 없음') · 지주사(H) 회사 가중치 추이(운용 요약의 시장 칸 `holding`) ·
 * 종목 여정(한 종목의 추천 → 결정 → 주문 → 가상 체결 → KIS 주문). 값은 배치가 적은 그대로 — 웹은 고르고 줄 세우고 글을 만든다.
 */

import type { Series } from "@/components/EquityChart";
import { amountText, statusText } from "@/lib/kisPaper";
import type { Attribution } from "@/lib/paper";

/** 계좌마다 한 번 — 바깥이 평가 행 전체를 훑고 행마다 하위 질의를 돌면 읽는 양이 계좌 행 수의 제곱까지 컸다(25.1630 교차검증 [권고] 3) */
export const LAB_ATTRIBUTION_SQL = `SELECT d.account_id, d.as_of, d.attribution_json, a.name
FROM paper_accounts a CROSS JOIN paper_daily d
WHERE d.account_id = a.id AND d.as_of = COALESCE(
  (SELECT MAX(x.as_of) FROM paper_daily x WHERE x.account_id = a.id AND x.attribution_json IS NOT NULL),
  (SELECT MAX(x.as_of) FROM paper_daily x WHERE x.account_id = a.id))
ORDER BY d.account_id`;
/** 운용 요약 최근 120일 — 시장 칸의 지주사 기록만 꺼낸다(기본키 역순이라 120행) */
export const LAB_HOLDING_DAYS = 120;
export const LAB_HOLDING_SQL = `SELECT as_of,
  json_extract(summary_json, '$.markets.KR.holding') AS kr, json_extract(summary_json, '$.markets.KR.run_at') AS kr_run,
  json_extract(summary_json, '$.markets.US.holding') AS us, json_extract(summary_json, '$.markets.US.run_at') AS us_run
FROM paper_desk ORDER BY as_of DESC LIMIT ${LAB_HOLDING_DAYS}`;

export interface LabAttribution { id: string; name: string; as_of: string; attribution: Attribution | null }
export interface HoldingMemo { weights?: Record<string, number>; samples?: Record<string, number>; recommended?: number; eligible?: number; chosen?: number; error?: string }
export interface HoldingPoint { market: "KR" | "US"; run_at: string; memo: HoldingMemo }

/** 운용 요약 줄들 → 시장별 지주사 기록(실행 시각 순, 이어받은 같은 칸은 한 번 — 25.1503 이어받기) */
export function holdingPoints(rows: Array<{ kr: string | null; kr_run: string | null; us: string | null; us_run: string | null }>): HoldingPoint[] {
  const 본 = new Set<string>();
  const out: HoldingPoint[] = [];
  for (const r of rows) {
    for (const [market, raw, run] of [["KR", r.kr, r.kr_run], ["US", r.us, r.us_run]] as const) {
      if (!raw || !run || 본.has(`${market}|${run}`)) continue;
      본.add(`${market}|${run}`);
      try {
        const memo = JSON.parse(raw) as HoldingMemo;
        if (memo && typeof memo === "object") out.push({ market, run_at: run, memo });
      } catch {
        // 깨진 칸은 없는 셈
      }
    }
  }
  return out.sort((a, b) => a.run_at.localeCompare(b.run_at));
}

const 회사색: Record<string, string> = { A: "#dc2626", B: "#64748b", C: "#ea580c", D: "#16a34a", E: "#2563eb" };

/** 한 시장의 회사별 가중치 추이 — 날짜는 실행 시각의 한국 날짜 */
export function weightSeries(points: HoldingPoint[], market: "KR" | "US"): Series[] {
  const 회사 = new Map<string, Array<{ date: string; value: number }>>();
  for (const p of points) {
    if (p.market !== market || !p.memo.weights) continue;
    const 날 = new Date(p.run_at).toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
    for (const [c, w] of Object.entries(p.memo.weights)) {
      const 줄 = 회사.get(c) ?? [];
      // 같은 날 두 번이면 뒤 것
      if (줄.length && 줄[줄.length - 1].date === 날) 줄[줄.length - 1] = { date: 날, value: Number(w) };
      else 줄.push({ date: 날, value: Number(w) });
      회사.set(c, 줄);
    }
  }
  return [...회사.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([c, points]) => ({ label: `${c} 가중치`, color: 회사색[c] ?? "#9ca3af", points }));
}

// ── 종목 여정 ─────────────────────────────────────────────

/**
 * 같은 티커가 여럿일 수 있다 — 이전상장 옛 행(같은 코드, KOSDAQ↔KOSPI, status excluded)·미국 거래소 이동. 다섯까지, 활성 먼저.
 * 시장·상태를 함께 돌려줘 고를 수 있게(25.1509, 검토 3)
 */
export const JOURNEY_STOCK_SQL = `SELECT id, ticker, country, market, status, COALESCE(name_ko, name_en, ticker) AS name FROM stocks
WHERE ticker = ? ORDER BY status = 'active' DESC, id LIMIT 5`;
/** 다섯 원장 — 종목·날짜 색인(0086·signals)으로 최근 것부터, 원장마다 같은 줄 수 */
export const JOURNEY_LIMIT = 120;
export const JOURNEY_SIGNALS_SQL = `SELECT as_of_date AS day, horizon, signal_type, buy_zone_low, buy_zone_high, currency
FROM signals WHERE stock_id = ? ORDER BY as_of_date DESC LIMIT ${JOURNEY_LIMIT}`;
export const JOURNEY_DECISIONS_SQL = `SELECT id, account_id, market, decided_on AS day, kind, reason, ret_20d, bench_20d
FROM paper_decisions WHERE stock_id = ? ORDER BY decided_on DESC LIMIT ${JOURNEY_LIMIT}`;
export const JOURNEY_ORDERS_SQL = `SELECT o.id, o.account_id, o.market, o.decided_on AS day, o.side, o.order_type, o.limit_price, o.quantity, o.currency,
  o.fill_session, o.tranche, o.status, o.closed_on, o.close_note, f.fill_date, f.price AS fill_price, f.quantity AS fill_qty
FROM paper_orders o LEFT JOIN paper_fills f ON f.order_id = o.id WHERE o.stock_id = ? ORDER BY o.decided_on DESC LIMIT ${JOURNEY_LIMIT}`;
export const JOURNEY_BROKER_SQL = `SELECT id, account_id, market, trade_date AS day, side, quantity, order_type, limit_price, status, filled_qty, avg_price, message, reason
FROM paper_broker_orders WHERE stock_id = ? ORDER BY trade_date DESC LIMIT ${JOURNEY_LIMIT}`;

export interface JourneyRaw {
  signals: Array<{ day: string; horizon: string; signal_type: string; buy_zone_low: number; buy_zone_high: number; currency: string }>;
  decisions: Array<{ id: number; account_id: string; market: string; day: string; kind: string; reason: string; ret_20d: number | null; bench_20d: number | null }>;
  orders: Array<{
    id: number; account_id: string; market: string; day: string; side: string; order_type: string | null; limit_price: number | null; quantity: number; currency: string;
    fill_session: string; tranche: number; status: string; closed_on: string | null; close_note: string | null; fill_date: string | null; fill_price: number | null; fill_qty: number | null;
  }>;
  broker: Array<{
    id: number; account_id: string; market: string; day: string; side: string; quantity: number; order_type: string; limit_price: number | null; status: string;
    filled_qty: number | null; avg_price: number | null; message: string | null; reason: string;
  }>;
}

export type JourneyStep = "추천" | "결정" | "주문" | "가상 체결" | "안 참" | "KIS";
export interface JourneyEvent { day: string; step: JourneyStep; account: string | null; text: string; order: number }

const HORIZON: Record<string, string> = { short: "단기", mid: "중기", long: "장기" };
const SIDE: Record<string, string> = { buy: "매수", sell: "매도" };
const KIND: Record<string, string> = { buy: "사기로", sell: "팔기로", skip: "안 냄", blocked: "위험 점검이 막음", missed: "안 찬 회차", hold_flag: "플래그 무시하고 듦" };
const cur = (c: string): "KRW" | "USD" => (c === "USD" ? "USD" : "KRW");

/** 다섯 원장 → 시간순 사건(최근 것 먼저 — 같은 날이면 뒤 단계가 위: KIS → 체결 → 주문 → 결정 → 추천) */
export function journeyEvents(raw: JourneyRaw): JourneyEvent[] {
  const out: JourneyEvent[] = [];
  for (const s of raw.signals)
    out.push({
      day: s.day, step: "추천", account: null, order: 0,
      text: `${HORIZON[s.horizon] ?? s.horizon} 추천 · ${s.signal_type} · 매수 구간 ${amountText(s.buy_zone_low, cur(s.currency))}~${amountText(s.buy_zone_high, cur(s.currency))}`,
    });
  for (const d of raw.decisions) {
    const 뒤 = d.ret_20d != null ? ` · 20일 뒤 ${d.ret_20d >= 0 ? "+" : ""}${d.ret_20d.toFixed(1)}%${d.bench_20d != null ? ` (지수 ${d.bench_20d >= 0 ? "+" : ""}${d.bench_20d.toFixed(1)}%)` : ""}` : "";
    out.push({ day: d.day, step: "결정", account: d.account_id, order: 1, text: `${KIND[d.kind] ?? d.kind} — ${d.reason}${뒤}` });
  }
  for (const o of raw.orders) {
    const 값 = o.order_type === "market" || o.limit_price == null ? "시장가" : `지정가 ${amountText(o.limit_price, cur(o.currency))}`;
    out.push({
      day: o.day, step: "주문", account: o.account_id, order: 2,
      text: `${SIDE[o.side] ?? o.side} ${o.quantity}주 · ${값}${o.tranche > 1 ? ` · ${o.tranche}차` : ""} · ${o.fill_session} 장`,
    });
    if (o.fill_date && o.fill_price != null)
      out.push({ day: o.fill_date, step: "가상 체결", account: o.account_id, order: 3, text: `${SIDE[o.side] ?? o.side} ${o.fill_qty ?? o.quantity}주 @ ${amountText(o.fill_price, cur(o.currency))}` });
    else if (o.status === "expired")
      out.push({ day: o.closed_on ?? o.fill_session, step: "안 참", account: o.account_id, order: 2.5, text: `${SIDE[o.side] ?? o.side} 안 참${o.close_note ? ` — ${o.close_note}` : ""}` });
  }
  for (const b of raw.broker) {
    // 통화는 시장으로 — 미국은 달러(25.1509, 검토 6). 계좌는 원장의 것 — 베끼는 계좌는 바뀔 수 있다(검토 2)
    const 체결 = b.filled_qty ? ` · 체결 ${b.filled_qty}주${b.avg_price != null ? ` @ ${amountText(b.avg_price, b.market === "US" ? "USD" : "KRW")}` : ""}` : "";
    out.push({
      day: b.day, step: "KIS", account: b.account_id, order: 4,
      text: `${b.reason} ${SIDE[b.side] ?? b.side} ${b.quantity}주 · ${statusText(b.status)}${체결}${b.status === "rejected" && b.message ? ` — ${b.message}` : ""}`,
    });
  }
  // 같은 날·같은 단계는 계좌 순 — 원장 행 순서(rowid)에 기대지 않게
  return out.sort((x, y) => y.day.localeCompare(x.day) || y.order - x.order || (x.account ?? "").localeCompare(y.account ?? ""));
}

/**
 * 여정을 자를 날 — 줄 수 한도까지 꽉 찬 원장들의 가장 오래된 날 가운데 가장 늦은 날(25.1509, 검토 1).
 * 원장마다 하루 줄 수가 달라(결정 원장은 계좌마다 날마다, 주문은 드묾) 120줄이 덮는 기간이 다르다 — 그대로 섞으면
 * 오래된 주문은 보이는데 그날의 결정·추천은 없는 여정이 된다. 그 날보다 앞선 사건은 버리고 "그 전은 잘림" 을 보인다. 꽉 찬 원장이 없으면 null
 */
export function journeyCut(raw: JourneyRaw, limit = JOURNEY_LIMIT): string | null {
  const 꽉 = [raw.signals, raw.decisions, raw.orders, raw.broker].filter((x) => x.length >= limit);
  const 날 = 꽉.map((x) => x.reduce((m, r) => (r.day < m ? r.day : m), x[0].day));
  return 날.length ? 날.reduce((a, b) => (a > b ? a : b)) : null;
}
