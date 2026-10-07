/**
 * 텔레그램 질의응답 (docs/telegram-qa.md, docs/infra.md 25.1004).
 *
 * 봇에게 종목 이름·티커를 보내면 DB 에 있는 값만으로 답한다 — 유료 LLM 을 쓰지 않는다(CLAUDE.md). 문장은 템플릿이고
 * 숫자는 DB 행 그대로다. **본인 대화방(`TELEGRAM_CHAT_ID`)에서 온 말에만** 답한다(웹훅 경로가 확인한다).
 * 읽기만 한다 — 점수·매매·설정을 바꾸는 명령은 두지 않는다.
 */
import type { ResultSet, SqlValue } from "@/lib/db";

export type Exec = (sql: string, args?: SqlValue[]) => Promise<ResultSet>;

export type Query =
  | { kind: "help" }
  | { kind: "holdings" }
  | { kind: "alerts" }
  | { kind: "stock"; q: string };

/** 한 번에 찾는 종목 후보 수. 여럿이면 이름만 보인다 */
export const MAX_MATCHES = 5;
/** 질문 글자 수 상한 — 긴 글은 종목 이름이 아니다 */
export const MAX_QUERY_LEN = 30;

export const HELP = [
  "물어볼 수 있는 것",
  "· 종목 이름이나 티커 (예: 삼성전자, 005930, AAPL) — 점수·신호·보유·매도 플래그",
  "· /보유 — 보유 종목 손익률",
  "· /알림 — 오늘 장중 알림",
  "· /도움 — 이 안내",
  "읽기만 합니다. 매매·설정은 앱에서 하세요.",
].join("\n");

export function parseQuery(text: string): Query | null {
  const t = text.trim().replace(/@\w+$/, "");
  if (!t) return null;
  const cmd = t.split(/\s+/)[0].toLowerCase();
  if (["/start", "/help", "/도움", "도움", "?"].includes(cmd)) return { kind: "help" };
  if (["/보유", "/holdings", "보유"].includes(cmd)) return { kind: "holdings" };
  if (["/알림", "/alerts", "알림"].includes(cmd)) return { kind: "alerts" };
  const q = (cmd === "/종목" || cmd === "/stock" ? t.split(/\s+/).slice(1).join(" ") : t).trim();
  if (!q || q.startsWith("/") || q.length > MAX_QUERY_LEN) return { kind: "help" };
  return { kind: "stock", q };
}

function rows<T>(rs: ResultSet): T[] {
  return rs.rows.map((r) => Object.fromEntries(rs.columns.map((c, i) => [c, r[i]])) as T);
}

const HORIZON: Record<string, string> = { short: "단기", mid: "중기", long: "장기" };
const LEVEL: Record<string, string> = { red: "적", yellow: "황", green: "녹" };

function n(x: unknown, digits = 0): string {
  return typeof x === "number" && Number.isFinite(x) ? x.toLocaleString("ko-KR", { maximumFractionDigits: digits }) : "-";
}

/** 이름·티커로 찾는다. 티커가 정확히 같으면 그것 하나, 아니면 이름 앞부분이 같은 것들 */
export const FIND_SQL = `SELECT id, ticker, country, COALESCE(name_ko, name_en, ticker) AS name FROM stocks
WHERE status = 'active' AND (UPPER(ticker) = UPPER(?) OR name_ko = ? OR UPPER(name_en) = UPPER(?)
  OR name_ko LIKE ? || '%' OR UPPER(name_en) LIKE UPPER(?) || '%')
ORDER BY CASE WHEN UPPER(ticker) = UPPER(?) OR name_ko = ? OR UPPER(name_en) = UPPER(?) THEN 0 ELSE 1 END, LENGTH(name_ko), ticker
LIMIT ${MAX_MATCHES}`;

export async function answer(query: Query, exec: Exec): Promise<string> {
  if (query.kind === "help") return HELP;
  if (query.kind === "holdings") return holdings(exec);
  if (query.kind === "alerts") return alerts(exec);
  const q = query.q;
  const found = rows<{ id: number; ticker: string; country: string; name: string }>(
    await exec(FIND_SQL, [q, q, q, q, q, q, q, q]),
  );
  if (found.length === 0) return `"${q}" 에 맞는 종목이 없습니다. 이름이나 티커를 다시 보내 주세요`;
  const exact = found.filter((f) => f.ticker.toUpperCase() === q.toUpperCase() || f.name === q);
  if (exact.length !== 1 && found.length > 1) {
    return `여럿이 맞습니다 — 하나를 골라 다시 보내 주세요\n${found.map((f) => `· ${f.name} (${f.ticker})`).join("\n")}`;
  }
  return stock(exact[0] ?? found[0], exec);
}

async function stock(s: { id: number; ticker: string; country: string; name: string }, exec: Exec): Promise<string> {
  const [scoreRs, priceRs, sigRs, posRs, flagRs] = await Promise.all([
    exec(
      `SELECT as_of_date, total_score, rank_in_market, factor_scores, skip_reason FROM scores
       WHERE stock_id = ? ORDER BY as_of_date DESC, calc_version DESC LIMIT 1`,
      [s.id],
    ),
    exec("SELECT date, close FROM prices WHERE stock_id = ? ORDER BY date DESC LIMIT 1", [s.id]),
    exec(
      `SELECT as_of_date, horizon, buy_zone_low, buy_zone_high, target_price, stop_price FROM signals
       WHERE stock_id = ? AND as_of_date = (SELECT MAX(as_of_date) FROM signals WHERE stock_id = ?)`,
      [s.id, s.id],
    ),
    exec("SELECT quantity, avg_price, unrealized_pnl_krw, cost_krw, price_date FROM positions WHERE stock_id = ?", [s.id]),
    exec(
      `SELECT level, rationale_text FROM sell_flags WHERE stock_id = ? AND is_active = 1
       AND as_of_date = (SELECT MAX(as_of_date) FROM sell_flags)`,
      [s.id],
    ),
  ]);
  const cur = s.country === "US" ? 2 : 0;
  const out = [`${s.name} (${s.ticker})`];
  const p = rows<{ date: string; close: number }>(priceRs)[0];
  if (p) out.push(`종가 ${n(p.close, cur)} (${p.date})`);
  const sc = rows<{ as_of_date: string; total_score: number | null; rank_in_market: number | null; factor_scores: string; skip_reason: string | null }>(scoreRs)[0];
  if (!sc) out.push("점수: 아직 없음 (유니버스 밖이거나 계산 전)");
  else if (sc.total_score == null) out.push(`점수: 계산 못 함 — ${sc.skip_reason ?? "사유 없음"} (${sc.as_of_date})`);
  else {
    let 팩터 = "";
    try {
      const f = JSON.parse(sc.factor_scores) as Record<string, number | null>;
      const 이름: Record<string, string> = { value: "밸류", quality: "퀄리티", growth: "성장", momentum: "모멘텀", risk: "리스크" };
      팩터 = Object.entries(이름).map(([k, v]) => `${v} ${f[k] == null ? "-" : n(f[k])}`).join(" · ");
    } catch {
      팩터 = "";
    }
    out.push(`종합 ${n(sc.total_score, 1)}점${sc.rank_in_market ? ` · 시장 ${sc.rank_in_market}위` : ""} (${sc.as_of_date})`);
    if (팩터) out.push(`  ${팩터}`);
  }
  const sigs = rows<{ as_of_date: string; horizon: string; buy_zone_low: number; buy_zone_high: number; target_price: number | null; stop_price: number | null }>(sigRs);
  for (const g of sigs) {
    out.push(
      `매수 신호 ${HORIZON[g.horizon] ?? g.horizon} (${g.as_of_date}): 구간 ${n(g.buy_zone_low, cur)}~${n(g.buy_zone_high, cur)}` +
        `${g.target_price ? ` · 목표 ${n(g.target_price, cur)}` : ""}${g.stop_price ? ` · 손절 ${n(g.stop_price, cur)}` : ""}`,
    );
  }
  if (sigs.length === 0) out.push("매수 신호: 최근 없음");
  const pos = rows<{ quantity: number; avg_price: number; unrealized_pnl_krw: number | null; cost_krw: number; price_date: string | null }>(posRs)[0];
  if (pos) {
    const 률 = pos.unrealized_pnl_krw != null && pos.cost_krw > 0 ? ` · 평가손익률 ${n((pos.unrealized_pnl_krw / pos.cost_krw) * 100, 1)}%` : "";
    out.push(`보유 ${n(pos.quantity, 4)}주 · 평균 ${n(pos.avg_price, cur)}${률}${pos.price_date ? ` (${pos.price_date})` : ""}`);
  }
  for (const f of rows<{ level: string; rationale_text: string }>(flagRs)) {
    out.push(`매도 플래그(${LEVEL[f.level] ?? f.level}): ${f.rationale_text}`);
  }
  out.push("근거표는 앱의 종목 화면에서 펼쳐 보세요");
  return out.join("\n");
}

async function holdings(exec: Exec): Promise<string> {
  const rs = rows<{ name: string; ticker: string; unrealized_pnl_krw: number | null; cost_krw: number; weight_pct: number | null }>(
    await exec(
      `SELECT COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.ticker, p.unrealized_pnl_krw, p.cost_krw, p.weight_pct
       FROM positions p JOIN stocks s ON s.id = p.stock_id
       ORDER BY CASE WHEN p.cost_krw > 0 THEN p.unrealized_pnl_krw * 1.0 / p.cost_krw END DESC NULLS LAST LIMIT 20`,
    ),
  );
  if (rs.length === 0) return "보유 종목이 없습니다";
  return [
    `보유 ${rs.length}종목 (손익률 순)`,
    ...rs.map((r) => {
      const 률 = r.unrealized_pnl_krw != null && r.cost_krw > 0 ? `${n((r.unrealized_pnl_krw / r.cost_krw) * 100, 1)}%` : "평가 없음";
      return `· ${r.name} ${률}${r.weight_pct != null ? ` · 비중 ${n(r.weight_pct, 1)}%` : ""}`;
    }),
  ].join("\n");
}

async function alerts(exec: Exec): Promise<string> {
  const rs = rows<{ name: string; message: string; trade_date: string }>(
    await exec(
      `SELECT COALESCE(s.name_ko, s.name_en, s.ticker) AS name, a.message, a.trade_date FROM alerts a
       JOIN stocks s ON s.id = a.stock_id
       WHERE a.trade_date = (SELECT MAX(trade_date) FROM alerts) ORDER BY a.created_at DESC LIMIT 20`,
    ),
  );
  if (rs.length === 0) return "장중 알림 기록이 없습니다";
  return [`장중 알림 (${rs[0].trade_date}, 최근 ${rs.length}건)`, ...rs.map((r) => `· ${r.name}: ${r.message}`)].join("\n");
}
