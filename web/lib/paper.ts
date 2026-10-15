/**
 * 모의 투자 계좌 화면·입금 (docs/paper.md 3·5장, docs/infra.md 25.1446).
 *
 * 사용자 입력은 **입금뿐**이다. 매매는 배치의 규칙서가 한다(`batch/jobs/paper.py`). 총 투자금액은 입금 장부의 합 — 화면은
 * 장부를 그대로 더해 보이고, 배치가 마지막으로 적은 평가(`paper_daily`)와 어긋나면 그 까닭을 함께 적는다.
 * 실제 매매(`trades`)·보유(`positions`)는 읽지도 쓰지도 않는다.
 */

import { z } from "zod";
import { localDate } from "@/lib/market";

/** 입금 한 번의 범위(원) — `batch/services/paper.py` 와 같은 값 (docs/paper.md 5장) */
export const DEPOSIT_MIN_KRW = 10_000;
export const DEPOSIT_MAX_KRW = 1_000_000_000;
/** 미체결 매수 예약에 얹는 수수료 여유·달러 환율 여유 — `batch/services/paper.py` 의 FEE_BUFFER·FX_BUFFER 와 같다 */
export const FEE_BUFFER = 0.01;
export const FX_BUFFER = 0.03;

/** 화면이 요청마다 만드는 키. 두 번 누름은 같은 키라 장부에 한 번만 들어간다 (docs/paper.md 5장) */
const requestKey = z.string().regex(/^[A-Za-z0-9-]{8,64}$/, "요청 키가 올바르지 않습니다");

export const depositSchema = z.object({
  amount_krw: z
    .number({ message: "금액은 숫자여야 합니다" })
    .int("금액은 원 단위 정수여야 합니다")
    .min(DEPOSIT_MIN_KRW, `입금은 ${DEPOSIT_MIN_KRW.toLocaleString("ko-KR")}원 이상입니다`)
    .max(DEPOSIT_MAX_KRW, `입금은 한 번에 ${DEPOSIT_MAX_KRW.toLocaleString("ko-KR")}원 이하입니다`),
  memo: z.string().trim().max(100, "메모는 100자 이하입니다").optional().default(""),
  request_key: requestKey,
});

export const cancelSchema = z.object({ request_key: requestKey });

export const PAPER_ACCOUNTS = "SELECT id, name, sell_rule, rule_version FROM paper_accounts ORDER BY id";
/** 총 투자금액 = 입금 장부 전체의 합(취소 행은 음수) */
export const PAPER_INVESTED = "SELECT COALESCE(SUM(amount_krw), 0) AS invested, COUNT(*) AS n FROM paper_cash_flows";
export const PAPER_FLOWS = `SELECT f.id, f.flow_date, f.amount_krw, f.kind, f.cancels_id, f.memo, f.created_at,
  (SELECT c.id FROM paper_cash_flows c WHERE c.cancels_id = f.id) AS cancelled_by
FROM paper_cash_flows f ORDER BY f.id DESC LIMIT 200`;
/** 계좌마다 장부 현금 = 입금 합 + 체결의 현금 영향 합 — 배치(`services/paper.cash`)와 같은 식 */
export const PAPER_CASH = `SELECT a.id AS account_id,
  (SELECT COALESCE(SUM(amount_krw), 0) FROM paper_cash_flows)
  + COALESCE((SELECT SUM(f.cash_krw) FROM paper_fills f WHERE f.account_id = a.id), 0) AS cash_krw
FROM paper_accounts a ORDER BY a.id`;
export const PAPER_OPEN_BUYS =
  "SELECT account_id, limit_price, quantity, currency FROM paper_orders WHERE status = 'open' AND side = 'buy'";
export const PAPER_LATEST_DAILY = `SELECT d.account_id, d.as_of, d.invested_krw, d.cash_krw, d.holdings_krw, d.equity_krw,
  d.pnl_krw, d.twr_index, d.kospi_krw, d.sp500_krw, d.positions_json, d.recon_ok, d.recon_note, d.unknown_json, d.updated_at,
  d.attribution_json, d.realized_krw, d.unrealized_krw
FROM paper_daily d
WHERE d.as_of = (SELECT MAX(x.as_of) FROM paper_daily x WHERE x.account_id = d.account_id)`;
export const PAPER_HISTORY = `SELECT account_id, as_of, invested_krw, equity_krw, twr_index, kospi_krw, sp500_krw
FROM paper_daily WHERE as_of >= ? ORDER BY as_of`;
/** 거래내역 — 계좌마다 화면이 거른다(두 계좌 합쳐 최근 500건). 매도의 실현손익은 배치가 체결 때 적었다 (25.1455) */
export const PAPER_FILLS = `SELECT f.id, f.account_id, f.stock_id, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name,
  f.side, f.fill_date, f.price, f.quantity, f.currency, f.fx_rate, f.fee, f.tax, f.slippage, f.cash_krw, f.realized_krw,
  o.reason, o.decided_on, o.rationale_json, o.close_note
FROM paper_fills f JOIN paper_orders o ON o.id = f.order_id JOIN stocks s ON s.id = f.stock_id
ORDER BY f.fill_date DESC, f.id DESC LIMIT 500`;
export const PAPER_OPEN_ORDERS = `SELECT o.id, o.account_id, o.market, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name,
  o.side, o.limit_price, o.quantity, o.currency, o.fill_session, o.tranche, o.reason
FROM paper_orders o JOIN stocks s ON s.id = o.stock_id WHERE o.status = 'open' ORDER BY o.id`;
export const PAPER_STOCK_NAMES = `SELECT s.id, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name
FROM json_each(?) j JOIN stocks s ON s.id = j.value`;
export const PAPER_FX_LATEST = "SELECT rate, date FROM fx_rates WHERE pair = 'USDKRW' ORDER BY date DESC LIMIT 1";

export const DEPOSIT_INSERT = `INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, memo, created_at, request_key)
VALUES (?, ?, 'deposit', ?, ?, ?) ON CONFLICT DO NOTHING`;
/** 취소 행 — 요청 키와 `cancels_id` 가 둘 다 유일이라 두 번 눌러도, 두 탭에서 눌러도 한 번만 들어간다 */
export const CANCEL_INSERT = `INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, cancels_id, memo, created_at, request_key)
VALUES (?, ?, 'cancel', ?, ?, ?, ?) ON CONFLICT DO NOTHING`;
export const FLOW_BY_ID = `SELECT f.id, f.amount_krw, f.kind, f.cancels_id,
  (SELECT COUNT(*) FROM paper_cash_flows c WHERE c.cancels_id = f.id) AS cancelled
FROM paper_cash_flows f WHERE f.id = ?`;
export const FLOW_BY_KEY = "SELECT id, amount_krw, kind FROM paper_cash_flows WHERE request_key = ?";

export interface OpenBuy {
  account_id: string;
  limit_price: number | null;
  quantity: number;
  currency: string;
}

/** 입금 날짜 = 넣은 날(한국 시각) (docs/paper.md 5장) */
export function kstToday(now: Date = new Date()): string {
  return localDate("KR", now);
}

/**
 * 계좌별 미체결 매수 예약(원) — 배치 `reserved_krw` 와 같은 식. 달러 주문인데 환율을 모르면 null(모르는 예약을 0 으로 치지 않는다).
 */
export function reservedByAccount(buys: OpenBuy[], usdKrw: number | null): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  for (const b of buys) {
    if (b.limit_price == null) continue;
    const prev = out[b.account_id] === undefined ? 0 : out[b.account_id];
    if (prev === null) continue;
    let 금액 = b.limit_price * b.quantity * (1 + FEE_BUFFER);
    if (b.currency !== "KRW") {
      if (!usdKrw) {
        out[b.account_id] = null;
        continue;
      }
      금액 *= usdKrw * (1 + FX_BUFFER);
    }
    out[b.account_id] = prev + 금액;
  }
  for (const k of Object.keys(out)) if (out[k] !== null) out[k] = Math.round(out[k] as number);
  return out;
}

/**
 * 그 입금을 취소할 수 있나 — 못 하면 까닭 (배치 `cancellable` 과 같은 규칙, docs/paper.md 5장).
 * `available` 은 계좌마다 (현금 − 미체결 매수 예약). 예약을 모르면(null) 막는다 — 모르는 것을 넉넉하다고 보지 않는다
 */
export function cancelBlock(
  flow: { amount_krw: number; kind: string; cancelled: number } | undefined,
  available: Record<string, number | null>,
): string | null {
  if (!flow) return "없는 입금입니다";
  if (flow.kind !== "deposit") return "취소 행은 다시 취소할 수 없습니다";
  if (Number(flow.cancelled) > 0) return "이미 취소한 입금입니다";
  const 모자람 = Object.entries(available)
    .filter(([, v]) => v === null || v < flow.amount_krw)
    .map(([k]) => k)
    .sort();
  if (모자람.length) return `그 돈으로 이미 사서 현금이 모자랍니다 (계좌 ${모자람.join(", ")})`;
  return null;
}

export interface DailyRow {
  account_id: string;
  as_of: string;
  invested_krw: number;
  cash_krw: number;
  holdings_krw: number;
  equity_krw: number;
  pnl_krw: number;
  twr_index: number;
  kospi_krw: number | null;
  sp500_krw: number | null;
  recon_ok: number;
  recon_note: string | null;
  updated_at: string;
  realized_krw?: number | null;
  unrealized_krw?: number | null;
}

export interface AccountView {
  id: string;
  /** 지금 장부로 — 총 입금액 · 현금 · 미체결 매수 예약 */
  invested: number;
  cash: number;
  reserved: number | null;
  /** 배치가 마지막으로 잰 보유 평가(그 날짜 기준) */
  holdings: number | null;
  asOf: string | null;
  equity: number | null;
  pnl: number | null;
  pnlPct: number | null;
  twrPct: number | null;
  kospiPct: number | null;
  sp500Pct: number | null;
  /** 그날까지 실현손익 합 · 보유 평가손익 합 — 배치가 적은 값 (25.1455) */
  realized: number | null;
  unrealized: number | null;
  /** 화면에 붉게 — 배치 검산이 어긋났거나 장부와 마지막 평가가 다르다 */
  notes: string[];
  reconOk: boolean;
}

/**
 * 계좌 요약 — **총 입금액·현금은 지금 장부로**, 보유 평가는 배치가 마지막으로 잰 값으로 (docs/paper.md 3장).
 * 마지막 평가 뒤에 입금·취소가 있으면 평가 총액은 지금 현금 + 그때 보유로 다시 더하고, 그 사실을 적는다(지어내지 않는다).
 */
export function accountView(
  id: string,
  invested: number,
  cash: number,
  reserved: number | null,
  daily: DailyRow | undefined,
): AccountView {
  const notes: string[] = [];
  if (!daily) {
    return {
      id, invested, cash, reserved, holdings: null, asOf: null, equity: invested > 0 ? cash : null,
      pnl: null, pnlPct: null, twrPct: null, kospiPct: null, sp500Pct: null, realized: null, unrealized: null,
      notes: invested > 0 ? ["아직 배치가 평가하지 않았습니다 — 다음 아침 리포트 때부터 매수·평가합니다"] : [], reconOk: true,
    };
  }
  const 보유 = Number(daily.holdings_krw);
  const 총액 = cash + 보유;
  const 손익 = 총액 - invested;
  if (Number(daily.invested_krw) !== invested) {
    const 차 = invested - Number(daily.invested_krw);
    notes.push(
      `${daily.as_of} 평가 뒤 입금·취소 ${차 > 0 ? "+" : ""}${차.toLocaleString("ko-KR")}원 — 다음 배치부터 매수 여력에 들어갑니다`,
    );
  }
  if (!Number(daily.recon_ok)) notes.push(`⚠️ 장부 검산 어긋남 (${daily.as_of}): ${daily.recon_note ?? "까닭 없음"}`);
  const 기준 = (v: number | null) => (v != null && Number(daily.invested_krw) > 0 ? (Number(v) / Number(daily.invested_krw) - 1) * 100 : null);
  return {
    id, invested, cash, reserved, holdings: 보유, asOf: daily.as_of, equity: 총액, pnl: 손익,
    pnlPct: invested > 0 ? (손익 / invested) * 100 : null,
    twrPct: (Number(daily.twr_index) - 1) * 100,
    kospiPct: 기준(daily.kospi_krw), sp500Pct: 기준(daily.sp500_krw),
    realized: daily.realized_krw == null ? null : Number(daily.realized_krw),
    unrealized: daily.unrealized_krw == null ? null : Number(daily.unrealized_krw),
    notes, reconOk: Boolean(Number(daily.recon_ok)),
  };
}

export const PAPER_TWR = "SELECT account_id, as_of, twr_index FROM paper_daily ORDER BY account_id, as_of";
/** 실제 포트폴리오의 시간가중 지수 — 비교에만 읽는다 (2026-10-15 사용자 지시 "실제 투자랑도 비교하는거 그냥 키자", 25.1448) */
export const REAL_TWR = "SELECT date, twr_index FROM portfolio_values WHERE date >= ? ORDER BY date";
/** 실제 계열을 모의 첫 기록보다 이만큼 앞부터 읽는다 — 배치 `REAL_LOOKBACK_DAYS` 와 같다 */
export const REAL_LOOKBACK_DAYS = 14;

type Point = [string, number];

/** 시간가중 지수의 [start, end] 변화율(%) — 배치 `window_change` 와 같은 규칙 */
export function windowChange(rows: Point[], start: string, end: string): { pct: number; start: string; end: string } | null {
  if (!rows.length) return null;
  const 앞 = rows.filter((r) => r[0] <= start);
  const 시작 = 앞.length ? 앞[앞.length - 1] : rows.find((r) => r[0] >= start);
  const 뒤 = rows.filter((r) => r[0] <= end);
  if (!시작 || !뒤.length || 뒤[뒤.length - 1][0] < 시작[0] || !시작[1]) return null;
  return { pct: (뒤[뒤.length - 1][1] / 시작[1] - 1) * 100, start: 시작[0], end: 뒤[뒤.length - 1][0] };
}

export interface RealCompare {
  start: string;
  end: string;
  realPct: number;
  paperPct: Record<string, number | null>;
}

/** 모의 계좌와 실제 포트폴리오를 같은 기간 시간가중으로 — 배치 `compare_real` 과 같은 규칙 (docs/paper.md 4장) */
export function compareReal(paper: Record<string, Point[]>, real: Point[]): RealCompare | null {
  const 모의 = Object.entries(paper).filter(([, v]) => v.length);
  if (!모의.length || !real.length) return null;
  const 시작 = [real[0][0], ...모의.map(([, v]) => v[0][0])].sort().at(-1)!;
  const 끝 = [real[real.length - 1][0], ...모의.map(([, v]) => v[v.length - 1][0])].sort()[0];
  if (끝 < 시작) return null;
  const 실제 = windowChange(real, 시작, 끝);
  if (!실제) return null;
  const paperPct: Record<string, number | null> = {};
  for (const [aid, rows] of 모의.sort(([a], [b]) => a.localeCompare(b))) paperPct[aid] = windowChange(rows, 시작, 끝)?.pct ?? null;
  return { start: 실제.start, end: 실제.end, realPct: 실제.pct, paperPct };
}

/** 성과 분해 한 칸 — 배치 `paper._초과` (docs/paper.md 10장, 25.1450) */
export interface AttrCell {
  n: number;
  avg: number | null;
  hit?: number | null;
}
export interface Attribution {
  horizon: number;
  selection: AttrCell;
  skipped: AttrCell;
  skip_by_reason: Record<string, AttrCell>;
  entry: AttrCell;
  missed: AttrCell;
  sold: AttrCell;
  held_flag: AttrCell;
  blocked?: AttrCell;
}
/** 배치 `ATTRIBUTION_MIN_SAMPLE` 과 같다 — 이보다 적은 칸은 "참고" */
export const ATTRIBUTION_MIN_SAMPLE = 30;

/** 한 칸의 글 — "+1.2%p (n=40, 이긴 비율 55%)", 표본이 적으면 ", 참고" */
export function attrCellText(c: AttrCell | undefined, unit = "%p"): string {
  if (!c || !c.n || c.avg == null) return "표본 없음";
  const 이김 = c.hit != null ? `, 이긴 비율 ${Math.round(c.hit)}%` : "";
  const 참고 = c.n < ATTRIBUTION_MIN_SAMPLE ? ", 참고" : "";
  return `${c.avg >= 0 ? "+" : ""}${c.avg.toFixed(unit === "%" ? 2 : 1)}${unit} (n=${c.n}${이김}${참고})`;
}

/** 모의 매매 멈춤 스위치 — 배치 `jobs/paper.HALT_SETTING_KEY` 와 같은 키 (docs/paper.md 11장, 25.1451) */
export const HALT_SETTING_KEY = "paper_halt";
export const HALT_UPSERT = `INSERT INTO settings (key, value, updated_at) VALUES ('${HALT_SETTING_KEY}', ?, ?)
ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`;
export const HALT_READ = `SELECT value FROM settings WHERE key = '${HALT_SETTING_KEY}'`;
export const haltSchema = z.object({ on: z.boolean(), why: z.string().trim().max(100).optional() });

/** 설정 값 → 켜졌나. 배치와 같은 읽기: {"on": true} 이거나 참 값 */
export function haltOn(raw: unknown): boolean {
  if (raw == null) return false;
  try {
    const v = typeof raw === "string" ? JSON.parse(raw) : raw;
    return typeof v === "object" && v !== null ? Boolean((v as { on?: unknown }).on) : Boolean(v);
  } catch {
    return false;
  }
}
