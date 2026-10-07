/**
 * 알림 센터 (docs/intraday.md 7장). 필터와 읽음 처리.
 *
 * 필터는 화면에서 건다 — 200건을 이미 읽어 왔고, 시장·트리거·읽음은 투자 수치가 아니라 표시 조건이다.
 * 읽음은 사용자 입력이라 배치가 바꾸지 않는다.
 */

export const TRIGGER_LABEL: Record<string, string> = {
  buy_zone: "매수 구간", watch_price: "관심 목표가", target: "목표가", stop: "손절선", spike_up: "급등", spike_down: "급락", volume: "거래량", disclosure: "공시",
  // 장 마감 뒤 배치가 남기는 보유 종목 시간외 단일가 (25.992, batch/jobs/kis_flows.py)
  after_hours: "시간외",
};
export const TRIGGER_ORDER = ["buy_zone", "watch_price", "target", "stop", "spike_up", "spike_down", "volume", "disclosure", "after_hours"] as const;

export interface AlertRow {
  id: number;
  market: string;
  trade_date: string;
  trigger_type: string;
  message: string;
  data: string;
  created_at: string;
  sent_at: string | null;
  ticker: string;
  stock_id: number;
  is_read: number;
}

export interface AlertFilter {
  market: "ALL" | "KR" | "US";
  trigger: "ALL" | string;
  unreadOnly: boolean;
}

export const DEFAULT_FILTER: AlertFilter = { market: "ALL", trigger: "ALL", unreadOnly: false };

export function filterAlerts<T extends Pick<AlertRow, "market" | "trigger_type" | "is_read">>(alerts: T[], f: AlertFilter): T[] {
  return alerts.filter(
    (a) =>
      (f.market === "ALL" || a.market === f.market) &&
      (f.trigger === "ALL" || a.trigger_type === f.trigger) &&
      (!f.unreadOnly || !a.is_read),
  );
}

export function unreadCount(alerts: Array<Pick<AlertRow, "is_read">>): number {
  return alerts.filter((a) => !a.is_read).length;
}

/**
 * 한 문장에 넣을 id 개수. D1 은 질의당 파라미터 100개가 한도다 (docs/infra.md 25.5).
 * 한 화면이 200개까지 읽어 오므로 문장을 나눠 보낸다.
 */
export const IDS_PER_STATEMENT = 90;

/** 읽음 갱신 문장들. 한도를 넘지 않게 나눈다. ids 가 비면 안 읽은 전부(문장 하나) */
export function readUpdateStatements(ids: number[], read: boolean): Array<{ sql: string; args: Array<number> }> {
  if (ids.length === 0) return [readUpdateSql([], read)];
  const unique = [...new Set(ids)].filter((id) => Number.isInteger(id) && id > 0).slice(0, 200);
  const out: Array<{ sql: string; args: Array<number> }> = [];
  for (let i = 0; i < unique.length; i += IDS_PER_STATEMENT) {
    out.push(readUpdateSql(unique.slice(i, i + IDS_PER_STATEMENT), read));
  }
  return out;
}

/** 읽음 갱신 문장. ids 가 비면 안 읽은 전부. 최대 200개 — 한 화면이 읽어 오는 양 */
export function readUpdateSql(ids: number[], read: boolean): { sql: string; args: Array<number> } {
  const value = read ? 1 : 0;
  if (ids.length === 0) {
    return { sql: `UPDATE alerts SET is_read = ${value} WHERE is_read = ${read ? 0 : 1}`, args: [] };
  }
  const unique = [...new Set(ids)].filter((id) => Number.isInteger(id) && id > 0).slice(0, 200);
  return {
    sql: `UPDATE alerts SET is_read = ${value} WHERE id IN (${unique.map(() => "?").join(", ")})`,
    args: unique,
  };
}


/**
 * 발송 칸 한 마디. `kst` 는 화면의 시각 표기 함수다.
 * **잡힌 알림(`claim:…`)은 "보내는 중"** (docs/infra.md 25.547, 교차검증) — 25.538 의 선점 표시가 `new Date("claim:…")` 로
 * 그려져 "발송 Invalid Date" 였다. 보낸 뒤 표시 쓰기가 실패하면 10분 동안 그대로 남는다.
 */
export function sentLabel(sent: string | null, kst: (iso: string) => string): string {
  // 조용시간 대기만이 아니다 — 발송이 실패해도 비어 있다 (docs/infra.md 25.338). 이유를 지어내지 않는다
  if (!sent) return "아직 안 보냄";
  if (sent === "quiet-skipped") return "보내지 않음 (조용시간)";
  if (sent.startsWith("claim:")) return "보내는 중 (10분 넘게 이대로면 다음 호출이 다시 보냄)";
  return `발송 ${kst(sent)}`;
}

/** "모두 읽음" 이 보낼 번호 — 지금 필터로 보이는 안 읽은 알림만, 서버 상한(200)까지 (25.584) */
export function unreadIdsShown<T extends Pick<AlertRow, "market" | "trigger_type" | "is_read"> & { id: number }>(
  alerts: T[],
  filter: AlertFilter,
): number[] {
  return filterAlerts(alerts, filter)
    .filter((a) => !a.is_read)
    .map((a) => a.id)
    .slice(0, 200);
}

/** 알림 목록 상한 — 경로가 최근 이만큼만 읽는다 */
export const ALERT_LIST_LIMIT = 200;
/** 전체 수와 안 읽은 수 — 목록 상한과 상관없이 (docs/infra.md 25.800) */
export const ALERT_TOTALS = `SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN COALESCE(is_read, 0) = 0 THEN 1 ELSE 0 END), 0) AS unread FROM alerts`;

/**
 * 알림 탭 이름 (25.800, 알림 감사 #2). 예전에는 최근 200건 안에서만 세어, 전체 350건·안 읽음 30건이어도 "알림 200 (안 읽음 10)" 으로 보였고
 * 200건 밖의 안 읽은 알림(보유 종목 손절·공시)은 화면에 없는데 다 본 것처럼 보였다. 전체 수를 모르면 예전처럼 목록 안에서 센다
 */
export function alertsTabLabel(
  shown: Array<Pick<AlertRow, "is_read">>, totals: { total: number; unread: number } | null,
): string {
  if (!totals) {
    const n = unreadCount(shown);
    return `알림 ${shown.length}${n ? ` (안 읽음 ${n})` : ""}`;
  }
  const 잘림 = totals.total > shown.length ? `최근 ${shown.length} / 전체 ${totals.total}` : `${totals.total}`;
  return `알림 ${잘림}${totals.unread ? ` (안 읽음 ${totals.unread})` : ""}`;
}

/** 목록 밖에 남은 안 읽은 알림 — 그 수를 말한다 (25.800) */
export function hiddenUnreadNote(shown: Array<Pick<AlertRow, "is_read">>, totals: { total: number; unread: number } | null): string | null {
  if (!totals) return null;
  const 밖 = totals.unread - unreadCount(shown);
  return 밖 > 0 ? `최근 ${shown.length}건 밖에 안 읽은 알림 ${밖}건이 더 있습니다 — 오래된 것부터 목록에서 빠집니다` : null;
}

/**
 * 미국 알림은 **그 장의 날짜**를 붙인다 (25.800, 알림 감사 #4). 한국 시각만 적으면 9/30장 04:00 KST 알림과 10/1장 23:00 KST 알림이
 * 둘 다 "10. 1." 로 찍혀 같은 날 같은 알림이 두 번 온 것처럼 보였다(하루 한 번은 현지 거래일 기준이다)
 */
export function sessionLabel(market: string, tradeDate: string | null): string {
  if (market !== "US" || !tradeDate) return "";
  const [, m, d] = tradeDate.split("-");
  return ` · ${Number(m)}/${Number(d)}장`;
}
