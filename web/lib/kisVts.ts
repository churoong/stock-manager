/**
 * KIS모의투자 웹 보기 B — 열 때 KIS 모의 도메인에서 잔고·오늘 체결을 **조회만** 한다 (docs/paper.md 15.7, docs/infra.md 25.1498).
 *
 * 지키는 것 (설계 검증 25.1496 [필수] ③④·[권장] 5·6·7):
 * - 모의 도메인을 글자 그대로 검사하고, tr_id 는 모의 **조회**(`V…R`)만 — 주문·토큰 발급 경로가 없다. 토큰은 배치가 받은 `api_tokens.name='kis_vts'` 를 읽기만
 *   (웹이 받으면 1분 1회 발급 제한에 배치가 걸려 그날 주문이 통째로 빠질 수 있다)
 * - 일일 배치가 도는 동안은 부르지 않는다 — 같은 앱키의 초당 한도를 나눠 쓰면 배치 주문(다시 보내지 않음)이 `EGW00201` 거부로 적힌다
 * - 같은 시장은 30초 안에 다시 부르지 않는다(`settings` 조건부 upsert 한 문장 — 원자적)
 * - 브라우저로는 고른 칸만 보낸다(계좌번호·토큰·원시 응답 없음). 오류는 KIS 코드나 예외 이름만
 */

export type VtsMarket = "KR" | "US";

/** 모의투자 REST 도메인 — 배치 `batch/sources/kis_vts.BASE` 와 같은 글자 */
export const KIS_VTS_BASE = "https://openapivts.koreainvestment.com:29443";
export const VTS_TR = {
  KR_BALANCE: "VTTC8434R",
  KR_CCLD: "VTTC0081R",
  US_PRESENT: "VTRP6504R",
  US_CCNL: "VTTS3035R",
} as const;
export const VTS_PATH = {
  KR_BALANCE: "/uapi/domestic-stock/v1/trading/inquire-balance",
  KR_CCLD: "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
  US_PRESENT: "/uapi/overseas-stock/v1/trading/inquire-present-balance",
  US_CCNL: "/uapi/overseas-stock/v1/trading/inquire-ccnl",
} as const;
/** 같은 시장을 다시 부를 수 있는 최소 간격 */
export const LIVE_MIN_GAP_MS = 30_000;
/** 시장과 상관없이 두 조회 사이 최소 간격 — 국내·미국 단추를 같은 초에 누르면 같은 앱키의 초당 한도에 걸렸다(25.1499, 검토 [참고] 9) */
export const LIVE_ANY_GAP_MS = 3_000;
export const LIVE_ANY_KEY = "kis_live_at_ALL";
/** 호출 사이 간격 — 배치(`MIN_INTERVAL` 1.0초)와 같게 */
export const LIVE_CALL_GAP_MS = 1_000;
export const LIVE_TIMEOUT_MS = 8_000;
/** 이보다 오래된 `running` 은 죽은 실행으로 본다(배치 시간 제한 30분 + 여유) */
export const BATCH_RUNNING_WINDOW_MS = 45 * 60_000;

export const LIVE_THROTTLE_SQL = `INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at WHERE settings.updated_at < ?`;
export const BATCH_RUNNING_SQL = `SELECT 1 FROM batch_runs WHERE job_name IN ('daily_kr', 'daily_us') AND status = 'running'
AND started_at >= ? LIMIT 1`;
export const VTS_SAVED_ROW_SQL = "SELECT token, expires_at FROM api_tokens WHERE name = 'kis_vts'";

export function throttleKey(market: VtsMarket): string {
  return `kis_live_at_${market}`;
}

/** (종합계좌 8자리, 상품코드) — 배치 `kis_vts.account` 와 같은 규칙. 8자리면 종합계좌 01 */
export function vtsAccount(env: Record<string, string | undefined> = process.env): { cano: string; prdt: string } | null {
  const raw = String(env.KIS_VTS_ACCOUNT ?? "").replace(/\D/g, "");
  if (raw.length === 8) return { cano: raw, prdt: "01" };
  if (raw.length === 10) return { cano: raw.slice(0, 8), prdt: raw.slice(8) };
  return null;
}

export function vtsConfigured(env: Record<string, string | undefined> = process.env): boolean {
  return Boolean(env.KIS_VTS_APP_KEY && env.KIS_VTS_APP_SECRET && vtsAccount(env));
}

/** 모의 도메인·모의 조회 tr_id 가 아니면 보내지 않는다. 도메인은 **글자 그대로** 적는다 */
export function vtsUrlOk(url: string, tr: string): boolean {
  return url.startsWith("https://openapivts.koreainvestment.com:29443/") && /^V[A-Z]{3}\d{4}R$/.test(tr);
}

const num = (v: unknown): number | null => {
  if (v == null || v === "") return null;
  const n = Number(String(v).replaceAll(",", ""));
  return Number.isFinite(n) ? n : null;
};
const int = (v: unknown): number => Math.trunc(num(v) ?? 0);

export interface LiveHolding { code: string; name: string | null; qty: number; avg: number | null; price: number | null; value: number | null; pnl: number | null; pnl_pct: number | null }
export interface LiveExecution { date: string; time: string | null; code: string; name: string | null; side: "buy" | "sell" | string; qty: number; filled: number; avg_price: number | null; order_price: number | null; note: string | null }
export interface LiveView {
  /** 첫 쪽만 읽었다 — KIS 가 다음 쪽이 있다고 했다(모의 잔고 한 쪽 20종목·체결 약 15건). 나머지는 배치 기록(A)에 */
  more?: boolean;
  market: VtsMarket;
  currency: "KRW" | "USD";
  fetched_at: string;
  holdings: LiveHolding[];
  account: { cash: number | null; holdings_value: number | null; total: number | null };
  executions: LiveExecution[];
}

function rows(payload: unknown, key: string): Array<Record<string, unknown>> {
  const v = (payload as Record<string, unknown> | null)?.[key];
  if (Array.isArray(v)) return v.filter((x): x is Record<string, unknown> => !!x && typeof x === "object");
  return v && typeof v === "object" ? [v as Record<string, unknown>] : [];
}

const side = (code: unknown): string => (String(code) === "01" ? "sell" : String(code) === "02" ? "buy" : String(code ?? ""));

/** 국내 잔고(VTTC8434R) + 오늘 체결(VTTC0081R) → 고른 칸. 현금은 총평가와 같은 D+2 (25.1497) */
export function parseKrLive(balance: unknown, ccld: unknown, fetchedAt: string): LiveView {
  const 합 = rows(balance, "output2")[0] ?? {};
  return {
    market: "KR",
    currency: "KRW",
    fetched_at: fetchedAt,
    holdings: rows(balance, "output1")
      .filter((r) => int(r.hldg_qty) > 0)
      .map((r) => ({
        code: String(r.pdno ?? "").trim(), name: r.prdt_name ? String(r.prdt_name) : null, qty: int(r.hldg_qty), avg: num(r.pchs_avg_pric),
        price: num(r.prpr), value: num(r.evlu_amt), pnl: num(r.evlu_pfls_amt), pnl_pct: num(r.evlu_pfls_rt),
      })),
    account: { cash: num(합.prvs_rcdl_excc_amt), holdings_value: num(합.scts_evlu_amt), total: num(합.tot_evlu_amt) },
    executions: rows(ccld, "output1")
      .filter((r) => String(r.odno ?? "").trim())
      .map((r) => ({
        date: String(r.ord_dt ?? ""), time: r.ord_tmd ? String(r.ord_tmd) : null, code: String(r.pdno ?? "").trim(),
        name: r.prdt_name ? String(r.prdt_name) : null, side: side(r.sll_buy_dvsn_cd), qty: int(r.ord_qty), filled: int(r.tot_ccld_qty),
        avg_price: num(r.avg_prvs), order_price: num(r.ord_unpr), note: int(r.rjct_qty) > 0 ? "거부" : String(r.cncl_yn ?? "").toUpperCase() === "Y" ? "취소" : null,
      })),
  };
}

/** 미국 체결기준현재잔고(VTRP6504R, 외화) + 오늘 체결(VTTS3035R) → 고른 칸(달러) */
export function parseUsLive(present: unknown, ccnl: unknown, fetchedAt: string): LiveView {
  const usd = rows(present, "output2").find((r) => String(r.crcy_cd ?? "").toUpperCase() === "USD") ?? {};
  const holdings = rows(present, "output1")
    .filter((r) => int(r.cblc_qty13) > 0)
    .map((r) => ({
      code: String(r.pdno ?? "").trim(), name: r.prdt_name ? String(r.prdt_name) : null, qty: int(r.cblc_qty13), avg: num(r.avg_unpr3),
      price: num(r.ovrs_now_pric1), value: num(r.frcr_evlu_amt2), pnl: num(r.evlu_pfls_amt2), pnl_pct: num(r.evlu_pfls_rt1),
    }));
  const 평가 = holdings.every((h) => h.value != null) ? holdings.reduce((a, h) => a + (h.value ?? 0), 0) : null;
  const cash = num(usd.frcr_dncl_amt_2);
  return {
    market: "US",
    currency: "USD",
    fetched_at: fetchedAt,
    holdings,
    account: { cash, holdings_value: 평가, total: cash != null && 평가 != null ? cash + 평가 : null },
    executions: rows(ccnl, "output")
      .filter((r) => String(r.odno ?? "").trim())
      .map((r) => ({
        date: String(r.ord_dt ?? ""), time: r.ord_tmd ? String(r.ord_tmd) : null, code: String(r.pdno ?? "").trim(),
        name: r.prdt_name ? String(r.prdt_name) : null, side: side(r.sll_buy_dvsn_cd), qty: int(r.ft_ord_qty), filled: int(r.ft_ccld_qty),
        avg_price: num(r.ft_ccld_unpr3) || null, order_price: num(r.ft_ord_unpr3),
        note: String(r.rjct_rson ?? "").trim().replace(/^0+$/, "") ? "거부" : null,
      })),
  };
}

/** KIS 응답에서 오류 코드만 — 문장(주소·계좌가 섞일 수 있다)은 보내지 않는다 */
export function kisErrorCode(payload: unknown): string | null {
  const p = payload as Record<string, unknown> | null;
  if (!p || typeof p !== "object") return "JSON 아님";
  return String(p.rt_cd) === "0" ? null : String(p.msg_cd ?? "오류");
}

/** 조회 인자 — 시장마다 두 번(잔고·오늘 체결). `day` 는 그 시장 현지 날짜 YYYY-MM-DD */
export function liveRequests(market: VtsMarket, acct: { cano: string; prdt: string }, day: string): Array<{ path: string; tr: string; params: Record<string, string> }> {
  const d = day.replaceAll("-", "");
  const a = { CANO: acct.cano, ACNT_PRDT_CD: acct.prdt };
  if (market === "KR")
    return [
      { path: VTS_PATH.KR_BALANCE, tr: VTS_TR.KR_BALANCE, params: { ...a, AFHR_FLPR_YN: "N", OFL_YN: "", INQR_DVSN: "02", UNPR_DVSN: "01", FUND_STTL_ICLD_YN: "N", FNCG_AMT_AUTO_RDPT_YN: "N", PRCS_DVSN: "00", CTX_AREA_FK100: "", CTX_AREA_NK100: "" } },
      { path: VTS_PATH.KR_CCLD, tr: VTS_TR.KR_CCLD, params: { ...a, INQR_STRT_DT: d, INQR_END_DT: d, SLL_BUY_DVSN_CD: "00", PDNO: "", CCLD_DVSN: "00", INQR_DVSN: "01", INQR_DVSN_3: "00", ORD_GNO_BRNO: "", ODNO: "", INQR_DVSN_1: "", EXCG_ID_DVSN_CD: "KRX", CTX_AREA_FK100: "", CTX_AREA_NK100: "" } },
    ];
  return [
    { path: VTS_PATH.US_PRESENT, tr: VTS_TR.US_PRESENT, params: { ...a, WCRC_FRCR_DVSN_CD: "02", NATN_CD: "840", TR_MKET_CD: "00", INQR_DVSN_CD: "00" } },
    { path: VTS_PATH.US_CCNL, tr: VTS_TR.US_CCNL, params: { ...a, PDNO: "", ORD_STRT_DT: d, ORD_END_DT: d, SLL_BUY_DVSN: "00", CCLD_NCCS_DVSN: "00", OVRS_EXCG_CD: "", SORT_SQN: "DS", ORD_DT: "", ORD_GNO_BRNO: "", ODNO: "", CTX_AREA_NK200: "", CTX_AREA_FK200: "" } },
  ];
}
