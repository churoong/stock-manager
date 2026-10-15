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
/** 시장가 매수(시가 일괄, C·R)의 예약 잣대 = 기준가 × (1 + 이 값) — `batch/services/paper.py` OPEN_BUY_BUFFER 와 같다 (25.1458) */
export const OPEN_BUY_BUFFER = 0.1;

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

export const PAPER_ACCOUNTS =
  "SELECT id, name, sell_rule, rule_version, entry_rule, top_n, selection, charter_json FROM paper_accounts ORDER BY id";

/**
 * 0075 가 적용되기 **전**의 같은 질의 (25.1458 재검증 권장 3). 마이그레이션은 다음 일일 배치가 적용하므로, 그 사이에 배포된 웹이
 * 새 칸(`entry_rule`·`ref_price`)을 읽으면 /paper 와 입금 취소가 "no such column" 500 이 된다. 칸이 없으면 이 질의로 돌아간다
 */
export const PAPER_ACCOUNTS_LEGACY = "SELECT id, name, sell_rule, rule_version FROM paper_accounts ORDER BY id";
export const PAPER_OPEN_BUYS_LEGACY =
  "SELECT account_id, limit_price, quantity, currency FROM paper_orders WHERE status = 'open' AND side = 'buy'";
export const PAPER_OPEN_ORDERS_LEGACY = `SELECT o.id, o.account_id, o.market, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name,
  o.side, o.limit_price, o.quantity, o.currency, o.fill_session, o.tranche, o.reason
FROM paper_orders o JOIN stocks s ON s.id = o.stock_id WHERE o.status = 'open' ORDER BY o.id`;

export function isMissingColumn(e: unknown): boolean {
  return /no such column/i.test(e instanceof Error ? e.message : String(e));
}
/** 새 질의를 먼저, 칸이 없다는 오류일 때만 옛 질의로. 다른 오류는 그대로 던진다 */
export async function withLegacy<T>(run: (sql: string) => Promise<T>, sql: string, legacy: string): Promise<T> {
  try {
    return await run(sql);
  } catch (e) {
    if (isMissingColumn(e)) return run(legacy);
    throw e;
  }
}

/** 계좌 규칙 칸 (migrations/0075, docs/paper.md 1장) — 화면은 칸을 글로 옮길 뿐 계좌 이름을 보지 않는다 */
export interface AccountRule {
  sell_rule: string;
  entry_rule?: string | null;
  top_n?: number | null;
  selection?: string | null;
  /** 판 2 투자사 헌장 (migrations/0077, docs/company.md) — 있으면 매수는 위원회 가결 */
  charter_json?: string | null;
}
const SELL_RULE_TEXT: Record<string, string> = {
  all: "모든 매도 플래그",
  core: "손절·목표도달·기간초과만",
  time: "기간초과만(손절·목표 무시)",
  value: "손절·목표도달·재무악화·점검",
  momentum: "손절·기간초과·재무악화·점검·감성급락(목표에서 안 팖)",
};
export function ruleText(a: AccountRule): string {
  let 헌장: { name?: string; agenda?: number; top_n?: number } | null = null;
  try {
    헌장 = a.charter_json ? JSON.parse(a.charter_json) : null;
  } catch {
    헌장 = null;
  }
  const 고르기 = 헌장
    ? `${헌장.name ?? "투자사"} 위원회 가결(안건 ${헌장.agenda ?? "?"}개 중 최대 ${헌장.top_n ?? a.top_n ?? 5}종목)`
    : a.selection === "random"
      ? "무작위 대조(같은 자리·비중)"
      : `점수 상위 ${a.top_n ?? 5}종목`;
  const 진입 = a.entry_rule === "open" ? "다음 장 시가에 한 번에" : "3회 분할 지정가";
  return `매수: ${고르기} · ${진입} — 매도: ${SELL_RULE_TEXT[a.sell_rule] ?? a.sell_rule}`;
}
/** 투자위원회 최근 회의록 — 회사·시장마다 마지막 날 한 장 (docs/company.md 3·5장, 25.1469). 0076 전에는 표가 없어 부르는 쪽이 오류를 삼킨다 */
export const PAPER_MEETINGS_LATEST = `SELECT m.account_id, m.market, m.decided_on, m.charter_hash, m.minutes_json FROM paper_meetings m
WHERE m.decided_on = (SELECT MAX(x.decided_on) FROM paper_meetings x WHERE x.account_id = m.account_id AND x.market = m.market)`;
/** 조사 자료 — 시장마다 가장 최근 기준일 한 벌 (회사가 달라도 같다) */
export const PAPER_DOSSIERS_LATEST = `SELECT d.market, d.as_of, d.stock_id, d.dossier_json FROM paper_dossiers d
WHERE d.as_of = (SELECT MAX(x.as_of) FROM paper_dossiers x WHERE x.market = d.market)`;
/** 부서 이름 — batch/services/company.py `DEPT_LABEL` 과 같은 목록(시험이 대조) */
export const DEPT_LABEL: Record<string, string> = {
  research: "리서치부",
  quant: "퀀트부",
  broker: "증권사리서치부",
  flows: "수급부",
  governance: "공시·지배구조부",
  sentiment: "뉴스·감성부",
  risk: "리스크관리부",
  macro: "시장전략부",
};
/** 부서 입장 한 칸 — [입장, 확신, (자료 없음 까닭)] → 글 */
export function stanceText(op: unknown[] | undefined): string {
  if (!op) return "";
  const [입장, 확신, 메모] = op as [number, number, string | undefined];
  if (메모) return "–";
  if (입장 > 0) return `▲${Math.round(Number(확신) * 100)}`;
  if (입장 < 0) return `▼${Math.round(Number(확신) * 100)}`;
  return "·";
}

/** 짝 비교 — 판 2(투자사)부터 회사 대 기준(B)과 기준 대 무작위. batch/services/paper.py `PAIRS` 와 같은 목록 (25.1468) */
export const PAIRS: ReadonlyArray<readonly [string, string, string]> = [
  ["A", "B", "가치투자사 대 기준"],
  ["C", "B", "모멘텀투자사 대 기준"],
  ["D", "B", "장기보유투자사 대 기준"],
  ["E", "B", "안정투자사 대 기준"],
  ["B", "R1", "기준 대 무작위"],
  ["B", "R2", "기준 대 무작위"],
];
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
  "SELECT account_id, order_type, limit_price, ref_price, quantity, currency FROM paper_orders WHERE status = 'open' AND side = 'buy'";
export const PAPER_LATEST_DAILY = `SELECT d.account_id, d.as_of, d.invested_krw, d.cash_krw, d.holdings_krw, d.equity_krw,
  d.pnl_krw, d.twr_index, d.kospi_krw, d.sp500_krw, d.positions_json, d.recon_ok, d.recon_note, d.unknown_json, d.updated_at,
  d.attribution_json, d.realized_krw, d.unrealized_krw
FROM paper_daily d
WHERE d.as_of = (SELECT MAX(x.as_of) FROM paper_daily x WHERE x.account_id = d.account_id)`;
export const PAPER_HISTORY = `SELECT account_id, as_of, invested_krw, equity_krw, twr_index, kospi_krw, sp500_krw
FROM paper_daily WHERE as_of >= ? ORDER BY as_of`;
/** 계좌마다 거래내역에 싣는 최근 체결 수 — 계좌가 일곱이라 합쳐 자르면 한 계좌가 다른 계좌 몫을 밀어냈다 (25.1458) */
export const FILLS_PER_ACCOUNT = 300;
/** 거래내역 — **계좌마다** 최근 `FILLS_PER_ACCOUNT` 건. 매도의 실현손익은 배치가 체결 때 적었다 (25.1455) */
export const PAPER_FILLS = `SELECT * FROM (
  SELECT f.id, f.account_id, f.stock_id, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name,
    f.side, f.fill_date, f.price, f.quantity, f.currency, f.fx_rate, f.fee, f.tax, f.slippage, f.cash_krw, f.realized_krw,
    o.reason, o.decided_on, o.rationale_json, o.close_note,
    ROW_NUMBER() OVER (PARTITION BY f.account_id ORDER BY f.fill_date DESC, f.id DESC) AS rn
  FROM paper_fills f JOIN paper_orders o ON o.id = f.order_id JOIN stocks s ON s.id = f.stock_id
) WHERE rn <= ${FILLS_PER_ACCOUNT} ORDER BY fill_date DESC, id DESC`;
export const PAPER_OPEN_ORDERS = `SELECT o.id, o.account_id, o.market, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name,
  o.side, o.order_type, o.limit_price, o.ref_price, o.quantity, o.currency, o.fill_session, o.tranche, o.reason
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
  order_type?: string;
  limit_price: number | null;
  ref_price?: number | null;
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
    // 시장가 매수(시가 일괄)는 기준가 × (1 + 여유) — 0 으로 세면 입금 취소 막기가 C·R 에 묶인 돈을 못 봤다 (25.1458, 검증 [필수] 5)
    const 단가 = b.order_type === "market" ? (b.ref_price ? b.ref_price * (1 + OPEN_BUY_BUFFER) : null) : b.limit_price;
    if (단가 == null) continue;
    const prev = out[b.account_id] === undefined ? 0 : out[b.account_id];
    if (prev === null) continue;
    let 금액 = 단가 * b.quantity * (1 + FEE_BUFFER);
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
