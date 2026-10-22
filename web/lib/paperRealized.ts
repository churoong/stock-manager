/**
 * 가상모의투자 기간 실현손익 (docs/paper.md 6장, docs/infra.md 25.1693).
 *
 * 2026-10-22 사용자 지시 "모의투자에 실현손익이나 기간실현손익을 보는게 없네? 이것도 당연히 있어야지" ·
 * "기간을 조회하면 해당 기간 내 일자별 실현손익도 나오면 좋겠어".
 *
 * 매도마다의 실현손익(평균 원가)과 그 가운데 환차 몫은 **배치가 체결 때 적은 값**이다(`paper_fills.realized_krw`·
 * `realized_fx_krw`, `batch/services/paper.realized`). 웹은 계산하지 않고(CLAUDE.md) 기간 안의 값을 더해 묶기만 한다.
 */

/** 기간 안 매도 — 체결일(그 시장 현지 거래일) 순 */
export const PAPER_REALIZED = `SELECT f.id, f.fill_date, f.stock_id, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name,
  f.currency, f.quantity, f.price, f.fee, f.tax, f.slippage, f.realized_krw, f.realized_fx_krw
FROM paper_fills f JOIN stocks s ON s.id = f.stock_id
WHERE f.account_id = ? AND f.side = 'sell' AND f.fill_date >= ? AND f.fill_date <= ? ORDER BY f.fill_date, f.id`;
/** 0090 이 적용되기 **전**(배치가 다음 아침 적용 — 그 사이 배포된 웹이 "no such column" 500 을 내지 않게, 25.1458 꼴) */
export const PAPER_REALIZED_LEGACY = `SELECT f.id, f.fill_date, f.stock_id, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name,
  f.currency, f.quantity, f.price, f.fee, f.tax, f.slippage, f.realized_krw, NULL AS realized_fx_krw
FROM paper_fills f JOIN stocks s ON s.id = f.stock_id
WHERE f.account_id = ? AND f.side = 'sell' AND f.fill_date >= ? AND f.fill_date <= ? ORDER BY f.fill_date, f.id`;

export interface RealizedRow {
  id: number;
  fill_date: string;
  stock_id: number;
  ticker: string;
  name: string;
  currency: string;
  quantity: number;
  price: number;
  fee: number;
  tax: number;
  slippage: number;
  realized_krw: number | null;
  realized_fx_krw: number | null;
}

export type PeriodKey = "this_month" | "last_month" | "d30" | "this_year" | "all" | "custom";
export const PERIODS: Array<[PeriodKey, string]> = [
  ["this_month", "이번 달"],
  ["last_month", "지난 달"],
  ["d30", "최근 30일"],
  ["this_year", "올해"],
  ["all", "전체"],
  ["custom", "직접"],
];
/** "전체" 의 시작 — 가상모의투자를 연 해(2026-10-15)보다 앞이면 된다 */
export const ALL_FROM = "2026-01-01";
const 날짜 = /^\d{4}-\d{2}-\d{2}$/;

/** 기간 → [시작, 끝] (둘 다 포함, YYYY-MM-DD). `today` 는 한국 날짜 */
export function periodRange(key: PeriodKey, today: string, custom?: { from: string; to: string }): [string, string] {
  const [y, m] = today.split("-").map(Number);
  const 달 = (yy: number, mm: number) => `${yy}-${String(mm).padStart(2, "0")}`;
  const 말일 = (yy: number, mm: number) => new Date(Date.UTC(yy, mm, 0)).getUTCDate();
  switch (key) {
    case "this_month":
      return [`${달(y, m)}-01`, today];
    case "last_month": {
      const [py, pm] = m === 1 ? [y - 1, 12] : [y, m - 1];
      return [`${달(py, pm)}-01`, `${달(py, pm)}-${String(말일(py, pm)).padStart(2, "0")}`];
    }
    case "d30":
      return [new Date(Date.parse(`${today}T00:00:00Z`) - 29 * 86_400_000).toISOString().slice(0, 10), today];
    case "this_year":
      return [`${y}-01-01`, today];
    case "all":
      return [ALL_FROM, today];
    default:
      return custom && 날짜.test(custom.from) && 날짜.test(custom.to) ? [custom.from, custom.to] : [today, today];
  }
}

/** 쿼리 인자 검사 — 날짜 꼴, 시작 ≤ 끝 */
export function validRange(from: string | null, to: string | null): from is string {
  return from != null && to != null && 날짜.test(from) && 날짜.test(to) && from <= to;
}

export interface RealizedGroup {
  key: string;
  label: string;
  n: number;
  realized: number;
  /** 환차 몫 합 — 해외 매도가 없으면 0. 모르는 매도가 있으면 아는 것만 */
  fx: number;
  wins: number;
  losses: number;
}
export interface RealizedReport {
  n: number;
  realized: number;
  fx: number;
  price: number;
  /** 해외 매도 가운데 환차 몫을 아직 모르는 수(0090 전 매도를 배치가 채우기 전) */
  fxUnknown: number;
  hasForeign: boolean;
  wins: number;
  losses: number;
  /** 매도 비용(수수료·세금·슬리피지) — 원화로 환산하지 않은 통화별 합. 매수 비용은 원가에 들어 실현손익에 이미 빠졌다 */
  costs: Record<string, number>;
  /** 실현손익이 비어 있는 매도(팔 때 장부 보유 없음) — 합에 넣지 않는다 */
  unknown: number;
  byDay: Array<RealizedGroup & { cumulative: number }>;
  byStock: RealizedGroup[];
}

function 빈묶음(key: string, label: string): RealizedGroup {
  return { key, label, n: 0, realized: 0, fx: 0, wins: 0, losses: 0 };
}

/** 기간 안 매도를 합치고 일자별·종목별로 묶는다 — 더하기만(값은 배치가 적은 그대로) */
export function realizedReport(rows: RealizedRow[]): RealizedReport {
  const 일: Map<string, RealizedGroup> = new Map();
  const 종목: Map<string, RealizedGroup> = new Map();
  const out: RealizedReport = {
    n: 0, realized: 0, fx: 0, price: 0, fxUnknown: 0, hasForeign: false, wins: 0, losses: 0, costs: {}, unknown: 0,
    byDay: [], byStock: [],
  };
  for (const r of [...rows].sort((a, b) => a.fill_date.localeCompare(b.fill_date) || a.id - b.id)) {
    const 해외 = r.currency !== "KRW";
    out.hasForeign ||= 해외;
    out.costs[r.currency] = (out.costs[r.currency] ?? 0) + Number(r.fee) + Number(r.tax) + Number(r.slippage);
    if (r.realized_krw == null) {
      out.unknown += 1;
      continue;
    }
    const v = Number(r.realized_krw);
    const 환차 = 해외 ? (r.realized_fx_krw == null ? null : Number(r.realized_fx_krw)) : 0;
    if (환차 == null) out.fxUnknown += 1;
    for (const [표, 열쇠, 이름] of [
      [일, r.fill_date, r.fill_date],
      [종목, String(r.stock_id), `${r.name} (${r.ticker})`],
    ] as const) {
      const g = 표.get(열쇠) ?? 빈묶음(열쇠, 이름);
      g.n += 1;
      g.realized += v;
      g.fx += 환차 ?? 0;
      g.wins += v > 0 ? 1 : 0;
      g.losses += v < 0 ? 1 : 0;
      표.set(열쇠, g);
    }
    out.n += 1;
    out.realized += v;
    out.fx += 환차 ?? 0;
    out.wins += v > 0 ? 1 : 0;
    out.losses += v < 0 ? 1 : 0;
  }
  // 환차 몫을 모르는 매도가 있으면 주가 몫도 모른다 — 아는 것끼리만 빼지 않는다
  out.price = out.fxUnknown ? NaN : out.realized - out.fx;
  let 누적 = 0;
  out.byDay = [...일.values()].map((g) => ({ ...g, cumulative: (누적 += g.realized) }));
  out.byStock = [...종목.values()].sort((a, b) => b.realized - a.realized || a.label.localeCompare(b.label));
  return out;
}
