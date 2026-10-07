/**
 * 일일 리포트 이력 (docs/reports.md).
 *
 * 배치가 텔레그램으로 보낸 본문을 daily_reports.summary_text 에 그대로 남긴다. 화면은 그 글을
 * 다시 그리지 않고 **그대로** 보여 준다 — 다시 그리면 텔레그램과 어긋난다. 재료(report_items)는
 * 표로 곁들여 종목 상세로 이어 준다. 웹은 계산하지 않는다.
 */

import { z } from "zod";

export const reportQuerySchema = z.object({
  market: z.enum(["KR", "US"]).default("KR"),
  /** 비우면 가장 최근 리포트 */
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
});
export type ReportQuery = z.infer<typeof reportQuerySchema>;

export const LATEST_REPORT = `SELECT id, market, trade_date, status, generated_at, sent_at, telegram_message_id,
  summary_text, warnings_json, batch_run_id
FROM daily_reports WHERE market = ? ORDER BY trade_date DESC, id DESC LIMIT 1`;

export const REPORT_BY_DATE = `SELECT id, market, trade_date, status, generated_at, sent_at, telegram_message_id,
  summary_text, warnings_json, batch_run_id
FROM daily_reports WHERE market = ? AND trade_date = ? ORDER BY id DESC LIMIT 1`;

/** 이력 목록. 날짜 고르기용이라 본문은 읽지 않는다 */
export const HISTORY_LIMIT = 60;
export const REPORT_HISTORY = `SELECT id, trade_date, status, sent_at, generated_at,
  -- 보냈는지 모름 표지만 본다 — 본문은 읽지 않는다 (docs/infra.md 25.504, 교차검증)
  CASE WHEN instr(COALESCE(warnings_json, ''), '발송 여부를 알 수 없습니다') > 0 THEN 1 ELSE 0 END AS send_unknown,
  -- 일부만 나간 것도 위쪽 표시와 같게 (docs/infra.md 25.511, 교차검증)
  CASE WHEN instr(COALESCE(warnings_json, ''), '조각만 보냈습니다') > 0 THEN 1 ELSE 0 END AS send_partial
FROM daily_reports WHERE market = ? ORDER BY trade_date DESC, id DESC LIMIT ${HISTORY_LIMIT}`;

export const REPORT_ITEMS = `SELECT i.id, i.section, i.stock_id, i.rank, i.payload_json, i.rationale_text,
  s.ticker, COALESCE(s.name_ko, s.name_en) AS name
FROM report_items i LEFT JOIN stocks s ON s.id = i.stock_id
WHERE i.report_id = ? ORDER BY i.section, i.rank`;

export interface ReportRow {
  id: number;
  market: string;
  trade_date: string;
  status: string;
  generated_at: string;
  sent_at: string | null;
  telegram_message_id: string | null;
  summary_text: string;
  warnings_json: string;
  batch_run_id: number | null;
}

export interface HistoryRow {
  id: number;
  trade_date: string;
  status: string;
  sent_at: string | null;
  generated_at: string;
  send_unknown?: number | null;
  send_partial?: number | null;
}

/**
 * 날짜 목록의 발송 한 마디 (docs/infra.md 25.504, 교차검증). 25.493 은 위쪽만 "보냈는지 모름" 으로 고쳐,
 * 같은 리포트가 날짜 목록에서는 "미발송" 이라 단정했다.
 */
export function historySendLabel(h: Pick<HistoryRow, "sent_at" | "send_unknown" | "send_partial">): string {
  if (h.send_unknown) return "발송 모름";
  if (h.send_partial && h.sent_at) return "일부 보냄";
  return h.sent_at ? "보냄" : "미발송";
}

export interface ItemRow {
  id: number;
  section: string;
  stock_id: number | null;
  rank: number;
  payload_json: string;
  rationale_text: string | null;
  ticker: string | null;
  name: string | null;
}

export interface ReportItem {
  id: number;
  section: string;
  stock_id: number | null;
  rank: number;
  payload: Record<string, unknown>;
  rationale_text: string | null;
  ticker: string | null;
  name: string | null;
}

export const SECTION_LABEL: Record<string, string> = {
  recommend: "1부 개별 종목",
  buy_signal: "2부 배분",
  sell_flag: "매도 플래그",
  notice: "참고·경고",
};
export const SECTION_ORDER = ["recommend", "buy_signal", "sell_flag", "notice"] as const;

export function parseItems(rows: ItemRow[]): ReportItem[] {
  return rows.map((r) => {
    let payload: Record<string, unknown> = {};
    try {
      const parsed = JSON.parse(r.payload_json);
      if (parsed && typeof parsed === "object") payload = parsed as Record<string, unknown>;
    } catch {
      // 깨진 재료는 빈 것으로. 본문은 따로 있어 화면이 무너지지 않는다
    }
    return { id: r.id, section: r.section, stock_id: r.stock_id, rank: r.rank, payload, rationale_text: r.rationale_text, ticker: r.ticker, name: r.name };
  });
}

export function parseWarnings(raw: string | null | undefined): string[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((w) => typeof w === "string") : [];
  } catch {
    return [];
  }
}

/** 발송 상태 글자. 색만으로 뜻을 전하지 않는다 */
/**
 * 배치가 텔레그램 리포트를 **일부만** 보냈을 때 남기는 경고의 표지 (batch/jobs/daily.py, docs/infra.md 25.414·25.427).
 * 두 곳이 같은 글자를 써야 한다 — `tests/test_daily_skip.py` 가 배치 쪽 문구를 대조한다.
 */
export const PARTIAL_SEND_MARK = "조각만 보냈습니다";

/** 텔레그램 응답 시간 초과로 **보냈는지 모를 때** 배치가 남기는 경고의 표지 (batch/jobs/daily.py, docs/infra.md 25.493) */
export const UNKNOWN_SEND_MARK = "발송 여부를 알 수 없습니다";

export function deliveryLabel(report: Pick<ReportRow, "sent_at" | "status"> & { warnings_json?: string | null }): string {
  // 보냈는지 모르면 sent_at 이 비어도 "못 보냄" 이라 단정하지 않는다 (25.493)
  if (report.warnings_json?.includes(UNKNOWN_SEND_MARK)) return "보냈는지 모름 (텔레그램 응답을 받지 못함 — 전체는 이 화면에)";
  if (!report.sent_at) return "만들었지만 텔레그램으로 못 보냄";
  // 일부만 나간 것은 경고가 있는 보통 발송과 다르다 — 뒷부분은 텔레그램에 없다 (25.427, 교차검증 지적)
  if (report.warnings_json?.includes(PARTIAL_SEND_MARK)) return "일부만 보냄 (나머지는 이 화면에)";
  return report.status === "partial" ? "보냄 (경고 있음)" : "보냄";
}

/**
 * 리포트 거래일 뒤의 정규장들. **리포트가 몇 번 빠졌는지**를 세는 재료다 (docs/infra.md 25.235).
 * `market_sessions` 는 지난 행을 지우지 않는다(`jobs/monitor_targets` 는 오늘 이후만 다시 쓴다).
 */
export const SESSIONS_AFTER = `SELECT date, open_utc FROM market_sessions WHERE market = ? AND date > ? ORDER BY date LIMIT 60`;

/**
 * 가장 새 리포트 뒤로 **나왔어야 하는데 없는 리포트 수**. 달력을 모르면 null.
 *
 * 리포트는 거래일 S 의 장 시작 전에 **직전 거래일 T** 를 다룬다(`trade_date = T`). 그러니 T 뒤의 첫 정규장
 * S 는 이 리포트 자신이 나온 날이고, 그 다음 정규장부터 장이 열렸는데도 새 리포트가 없으면 하나씩 빠진 것이다.
 *
 * **달력 날짜로 세면 안 된다** (25.235 이전의 `ageDays`). 월요일 아침 리포트는 거래일이 금요일이라 나오자마자
 * "3일 지난 리포트" 였고, 미국 리포트(21:27 KST)는 다음날 09:00 KST(UTC 자정)부터 저녁까지 매일 "2일 지난" 이었다.
 */
export function missedReports(
  sessions: Array<{ date: string; open_utc: string | null }>,
  now: Date = new Date(),
): number | null {
  if (sessions.length === 0) return null;
  const 열린 = sessions.filter((s) => s.open_utc && Date.parse(s.open_utc) <= now.getTime()).length;
  return Math.max(0, 열린 - 1);
}

/**
 * 리포트에 실린 매도 플래그의 **판정일** (docs/infra.md 25.342). 리포트 거래일이 아니다 —
 * 판정은 사용자의 오늘 날짜로 하고, 플래그 배치가 실패한 날에는 앞선 판정이 실린다.
 * 25.342 전 리포트에는 판정일이 실려 있지 않다. 그때는 거래일을 빌려 쓰지 않고 모른다고 한다.
 */
export function flagAsOf(payload: Record<string, unknown>): string | null {
  return typeof payload.as_of_date === "string" && payload.as_of_date ? payload.as_of_date : null;
}

/**
 * 이 리포트가 **그 시장의 가장 새 리포트인가** (docs/infra.md 25.343). 빠진 리포트 수는 이때만 센다(25.235).
 * 예전에는 "날짜를 안 골랐나" 로 갈랐다 — 목록에서 **가장 새 날짜를 직접 고르면** 같은 리포트인데
 * 배지가 사라졌다. 날짜를 골랐는지가 아니라 **무엇을 보고 있는지**로 가른다.
 */
export function isLatestReport(report: Pick<ReportRow, "trade_date"> | null, history: Pick<HistoryRow, "trade_date">[]): boolean {
  return report !== null && history.length > 0 && report.trade_date === history[0].trade_date;
}

/**
 * 리포트 1부 추천에 실린 근거표 (docs/infra.md 25.349). `null` 이면 **근거표를 싣기 전 리포트**라는 뜻이다 —
 * 빈 배열(근거 0줄, 빨간 경고를 그려야 하는 경우)과 구별한다.
 */
export function pickCriteria(payload: Record<string, unknown>): string | null {
  return Array.isArray(payload.criteria) ? JSON.stringify({ criteria: payload.criteria }) : null;
}

/**
 * 1부 추천의 가중치 흔들기 (docs/reports.md 3.2, docs/infra.md 25.947) — 열여섯 세계 가운데 몇에서 상위에 남았나.
 * 글(`robustness.stability_line`)과 같은 말을 한다. 옛 리포트·깨진 값이면 null — 지어내지 않는다.
 */
export function stabilityLabel(payload: Record<string, unknown>): { text: string; title: string } | null {
  const st = payload.stability;
  if (!st || typeof st !== "object") return null;
  const { kept, total, dropped_by, base_matches } = st as Record<string, unknown>;
  if (typeof kept !== "number" || typeof total !== "number" || total <= 0) return null;
  const dropped = Array.isArray(dropped_by) ? dropped_by.map(String) : [];
  const text = `흔들기 ${kept}/${total}${base_matches === false ? " (가중치 바뀜)" : ""}`;
  const title =
    dropped.length === 0
      ? "설정 가중치를 어떻게 흔들어도 상위에 남는다"
      : `${dropped.join(" · ")}이면 상위에서 빠진다${base_matches === false ? " — 가중치가 점수 계산 뒤 바뀌어 지금 설정 중심으로 흔든 값" : ""}`;
  return { text, title };
}

/** 첫 추천 종가 대비를 적는 띠 — 배치 `pick_history.RATIO_BAND` 와 같은 값 (docs/reports.md 3.3, 25.963) */
export const HISTORY_RATIO_BAND = [0.5, 2.0] as const;

/**
 * 1부 추천의 추천 이력 (docs/reports.md 3.3, docs/infra.md 25.950) — 몇 번째 추천이고 처음 본 날 종가 대비 얼마나 움직였나.
 * 글(`pick_history.history_line`)과 같은 말. 옛 리포트(이력을 싣기 전)·깨진 값이면 null — 지어내지 않는다.
 */
export function historyLabel(payload: Record<string, unknown>): string | null {
  const h = payload.history;
  if (!h || typeof h !== "object") return null;
  const { times, first_date, first_close, streak } = h as Record<string, unknown>;
  if (typeof times !== "number") return null;
  if (times <= 0) return "첫 추천";
  const 연속 = typeof streak === "number" && streak >= 1 ? ` (연속 ${streak + 1}일)` : "";
  let text = `${times + 1}번째 추천${연속}`;
  if (typeof first_date === "string" && first_date) {
    text += ` — 첫 추천 ${first_date.length === 10 ? first_date.slice(5) : first_date}`;
    const now = payload.close;
    if (typeof first_close === "number" && first_close > 0 && typeof now === "number" && now > 0) {
      const ratio = now / first_close;
      // 배치 `pick_history.RATIO_BAND` 와 같은 띠 (25.963) — 밖이면 분할·병합일 수 있어 대비를 적지 않는다
      if (ratio >= HISTORY_RATIO_BAND[0] && ratio <= HISTORY_RATIO_BAND[1]) {
        const pct = Number(((ratio - 1) * 100).toFixed(1));
        text += ` 대비 ${pct > 0 ? "+" : ""}${(pct === 0 ? 0 : pct).toFixed(1)}%`;
      } else {
        text += " (가격 단위가 바뀐 듯해 대비는 적지 않음 — 분할·병합)";
      }
    }
  }
  return text;
}
