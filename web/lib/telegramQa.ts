/**
 * 텔레그램 질의응답 (docs/telegram-qa.md, docs/infra.md 25.1004).
 *
 * 봇에게 종목 이름·티커를 보내면 DB 에 있는 값만으로 답한다 — 유료 LLM 을 쓰지 않는다(CLAUDE.md). 문장은 템플릿이고
 * 숫자는 DB 행 그대로다. **본인 대화방(`TELEGRAM_CHAT_ID`)에서 온 말에만** 답한다(웹훅 경로가 확인한다).
 * 읽기만 한다 — 점수·매매·설정을 바꾸는 명령은 두지 않는다.
 */
import type { ResultSet, SqlValue } from "@/lib/db";
import { SEARCH_LIST_TTL_MS, STOCK_SEARCH_LIST, type SearchRow } from "@/lib/portfolio";
import { MAX_SIGNAL_VERSION_ON, OWN_SIGNAL_DATES, POSITION_SUMMARY_VERSION, SIGNALS, positionStale } from "@/lib/stockDetail";
import { TRADES_VERSION } from "@/lib/portfolio";

export type Exec = (sql: string, args?: SqlValue[]) => Promise<ResultSet>;

export type Query =
  | { kind: "help" }
  | { kind: "holdings" }
  | { kind: "alerts" }
  | { kind: "stock"; q: string };

/** 한 번에 찾는 종목 후보 수. 여럿이면 이름만 보인다 */
export const MAX_MATCHES = 5;
/** `/보유` 에 보이는 줄 수. 넘으면 머리 줄이 전체 수를 말한다 (25.1126) */
export const HOLDINGS_LIMIT = 20;
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
  // `/종목@봇이름 AAPL` 처럼 명령 뒤에 붙은 봇 이름도 지운다 (25.1126) — 예전엔 끝에 붙은 것만 지워 안내로 빠졌다
  const t = text.trim().replace(/^(\/[^\s@]+)@\w+/, "$1").replace(/@\w+$/, "");
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

/**
 * 이름·티커로 찾는다. 티커·이름이 정확히 같으면 그것, 아니면 이름 앞부분이 같은 것들 (25.1004).
 *
 * **검색 목록을 함께 쓴다** (docs/infra.md 25.1126, 텔레그램 감사). 예전 `FIND_SQL` 은 `UPPER(ticker)`·이름 LIKE 라 색인을 못 타
 * 질문 한 번에 종목 표 전체(약 1만 행)를 훑었고(25.1032 가 앱 검색에서 없앤 모양), `status = 'active'` 만 보아 **들고 있던 종목이
 * 폐지·제외되면**(정리매매 — 정보가 가장 필요할 때) "맞는 종목이 없습니다" 였다(25.643 이 앱에서 고친 것). 앱 검색과 같은 목록
 * (`STOCK_SEARCH_LIST` — active 또는 거래한 적 있는 종목)을 `SEARCH_LIST_TTL_MS` 동안 메모리에 두고 거른다
 */
let 목록: { exec: Exec; rows: SearchRow[]; at: number } | null = null;

async function searchList(exec: Exec): Promise<SearchRow[]> {
  if (!목록 || 목록.exec !== exec || Date.now() - 목록.at > SEARCH_LIST_TTL_MS) {
    목록 = { exec, rows: rows<SearchRow>(await exec(STOCK_SEARCH_LIST)), at: Date.now() };
  }
  return 목록.rows;
}

type Found = { id: number; ticker: string; country: string; name: string; alts?: number[]; exact?: boolean };

/** 예전 `FIND_SQL` 과 같은 규칙: 정확히 같은 것 먼저, 그다음 이름 앞부분 — 한글 이름 길이·티커 순, 최대 `MAX_MATCHES` */
export function findStocks(list: SearchRow[], q: string): Found[] {
  // **한글 이름의 영문자도 대소문자를 가리지 않는다** (docs/infra.md 25.1206, 텔레그램 감사). 예전 `FIND_SQL` 의 LIKE 는 가리지
  // 않았는데 25.1126 이 메모리 비교로 옮기며 `sk하이닉스`·`lg전자`·`kb금융` 이 "맞는 종목이 없습니다" 가 됐다
  const Q = q.toUpperCase();
  const 한 = (r: SearchRow) => (r.name_ko ?? "").toUpperCase();
  const exact = (r: SearchRow) => r.ticker.toUpperCase() === Q || (r.name_ko != null && 한(r) === Q) || (r.name_en ?? "").toUpperCase() === Q;
  const hit = list.filter((r) => exact(r) || (r.name_ko != null && 한(r).startsWith(Q)) || (r.name_en ?? "").toUpperCase().startsWith(Q));
  const len = (r: SearchRow) => (r.name_ko == null ? Number.MAX_SAFE_INTEGER : r.name_ko.length);
  // 같은 티커·같은 이름의 행이 둘이면(25.529 전 갈라진 옛 행) **하나만** — 상장 중인 것을 남긴다 (25.1206). 예전엔 둘 다 나와
  // "여럿이 맞습니다" 가 되고 어떤 답을 보내도 다시 같은 목록이라 그 종목을 영영 물을 수 없었다
  hit.sort((a, b) =>
    Number(!exact(a)) - Number(!exact(b)) || len(a) - len(b) || (a.ticker < b.ticker ? -1 : a.ticker > b.ticker ? 1 : 0) ||
    Number(a.status !== "active") - Number(b.status !== "active"));
  // 묶인 다른 행의 번호는 `alts` 에 둔다 — 보유가 옛 행에 있으면 `answer` 가 그 행을 고른다
  const 묶음 = new Map<string, Found>();
  for (const r of hit) {
    const 열쇠 = `${r.country}|${r.ticker.toUpperCase()}|${r.name_ko ?? ""}|${r.name_en ?? ""}`;
    const 앞 = 묶음.get(열쇠);
    if (앞) (앞.alts ??= []).push(r.id);
    else 묶음.set(열쇠, { id: r.id, ticker: r.ticker, country: r.country, name: r.name_ko ?? r.name_en ?? r.ticker, exact: exact(r) });
  }
  return [...묶음.values()].slice(0, MAX_MATCHES);
}

export async function answer(query: Query, exec: Exec): Promise<string> {
  if (query.kind === "help") return HELP;
  if (query.kind === "holdings") return holdings(exec);
  if (query.kind === "alerts") return alerts(exec);
  const q = query.q;
  const found = findStocks(await searchList(exec), q);
  if (found.length === 0) return `"${q}" 에 맞는 종목이 없습니다. 이름이나 티커를 다시 보내 주세요`;
  // 정확히 같음은 `findStocks` 가 티커·한글·**영문** 이름으로 판정한 것을 쓴다 (docs/infra.md 25.1241) — 예전엔 대표 이름(한글 우선)만
  // 견줘, 영문 정식 이름으로 물으면 앞부분이 같은 다른 종목과 함께 "여럿이 맞습니다" 로 빠졌다
  const exact = found.filter((f) => f.exact);
  if (exact.length !== 1 && found.length > 1) {
    return `여럿이 맞습니다 — 하나를 골라 다시 보내 주세요\n${found.map((f) => `· ${f.name} (${f.ticker})`).join("\n")}`;
  }
  const 고른 = exact[0] ?? found[0];
  if (고른.alts?.length) {
    // 같은 티커·이름의 행이 여럿이면 보유가 있는 행 (25.1206) — 없으면 상장 중인 행(정렬 첫째)
    const ids = [고른.id, ...고른.alts];
    const 보유 = rows<{ stock_id: number }>(
      await exec(`SELECT stock_id FROM positions WHERE stock_id IN (${ids.map(() => "?").join(", ")})`, ids),
    )[0];
    if (보유) return stock({ ...고른, id: Number(보유.stock_id) }, exec);
  }
  return stock(고른, exec);
}

async function stock(s: { id: number; ticker: string; country: string; name: string }, exec: Exec): Promise<string> {
  const [scoreRs, priceRs, sigRs, posRs, flagRs, 늦음] = await Promise.all([
    exec(
      `SELECT as_of_date, total_score, rank_in_market, factor_scores, skip_reason FROM scores
       WHERE stock_id = ? ORDER BY as_of_date DESC, calc_version DESC LIMIT 1`,
      [s.id],
    ),
    exec("SELECT date, close FROM prices WHERE stock_id = ? ORDER BY date DESC LIMIT 1", [s.id]),
    latestSignals(exec, s.id, s.country),
    exec(
      `SELECT quantity, avg_price, currency, unrealized_pnl, cost, unrealized_pnl_krw, unrealized_price_pnl_krw,
       unrealized_fx_pnl_krw, cost_krw, price_date FROM positions WHERE stock_id = ?`,
      [s.id],
    ),
    exec(
      `SELECT level, rationale_text, as_of_date FROM sell_flags WHERE stock_id = ? AND is_active = 1
       AND as_of_date = (SELECT MAX(as_of_date) FROM sell_flags)`,
      [s.id],
    ),
    보유늦음(exec),
  ]);
  // 통화를 붙인다 (25.1126) — 미국 종목 값이 원화인지 달러인지 글만으로 알 수 없었다
  const 돈 = (x: unknown) => (s.country === "US" ? `$${n(x, 2)}` : `${n(x, 0)}원`);
  const out = [`${s.name} (${s.ticker})`];
  const p = rows<{ date: string; close: number }>(priceRs)[0];
  if (p) out.push(`종가 ${돈(p.close)} (${p.date})`);
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
  const sigs = sigRs;
  for (const g of sigs) {
    out.push(
      `매수 신호 ${HORIZON[g.horizon] ?? g.horizon} (${g.as_of_date}): 구간 ${돈(g.buy_zone_low)}~${돈(g.buy_zone_high)}` +
        `${g.target_price ? ` · 목표 ${돈(g.target_price)}` : ""}${g.stop_price ? ` · 손절 ${돈(g.stop_price)}` : ""}`,
    );
  }
  if (sigs.length === 0) out.push("매수 신호: 최근 없음");
  const pos = rows<Pos>(posRs)[0];
  if (pos) out.push(`보유 ${n(pos.quantity, 4)}주 · 평균 ${돈(pos.avg_price)}${pnlText(pos)}${pos.price_date ? ` (${pos.price_date})` : ""}`);
  if (늦음) out.push(STALE_NOTE);
  // 판정일을 붙인다 (25.1206) — 배치가 멈춘 날의 옛 플래그가 오늘 것처럼 읽혔다. 다른 줄은 모두 날짜가 있다
  for (const f of rows<{ level: string; rationale_text: string; as_of_date: string }>(flagRs)) {
    out.push(`매도 플래그(${LEVEL[f.level] ?? f.level}, ${f.as_of_date}): ${f.rationale_text}`);
  }
  out.push("근거표는 앱의 종목 화면에서 펼쳐 보세요");
  return out.join("\n");
}

type Pos = {
  quantity: number; avg_price: number; currency: string; unrealized_pnl: number | null; cost: number;
  unrealized_pnl_krw: number | null; unrealized_price_pnl_krw: number | null; unrealized_fx_pnl_krw: number | null;
  cost_krw: number; price_date: string | null;
};

/**
 * 평가손익률. **해외 종목은 주가 손익과 환차손익을 나눈다** (docs/infra.md 25.1126, CLAUDE.md 매매 규칙). 예전에는 원화 손익률
 * 하나만 보여 주가가 10% 빠진 미국 종목이 환율이 오른 덕에 "+3.8%" 로 읽혔다. 주가 손익률은 종목 통화 손익 ÷ 종목 통화 원가,
 * 환차손익은 원화 금액(배치가 나눠 둔 값 그대로)
 */
export function pnlText(p: Pos): string {
  if (p.currency === "KRW") {
    return p.unrealized_pnl_krw != null && p.cost_krw > 0 ? ` · 평가손익률 ${n((p.unrealized_pnl_krw / p.cost_krw) * 100, 1)}%` : "";
  }
  const 주가 = p.unrealized_pnl != null && p.cost > 0 ? `주가 ${n((p.unrealized_pnl / p.cost) * 100, 1)}%` : null;
  const 환차 = p.unrealized_fx_pnl_krw != null ? `환차 ${p.unrealized_fx_pnl_krw >= 0 ? "+" : ""}${n(p.unrealized_fx_pnl_krw)}원` : null;
  const 원화 = p.unrealized_pnl_krw != null && p.cost_krw > 0 ? `원화 합계 ${n((p.unrealized_pnl_krw / p.cost_krw) * 100, 1)}%` : null;
  const 조각 = [주가, 환차, 원화].filter(Boolean);
  return 조각.length ? ` · 평가손익 ${조각.join(" · ")}` : "";
}

type Sig = { as_of_date: string; horizon: string; buy_zone_low: number; buy_zone_high: number; target_price: number | null; stop_price: number | null };

/**
 * 이 종목의 마지막 신호 — **그 나라·그날의 가장 새 판**만 (25.466 을 봇에도, 25.1126). 예전엔 `MAX(as_of_date)` 의 모든 판이라
 * 판을 올린 날 새 판이 걸러 낸 종목에 옛 판의 매수 구간·목표가가 나가고, 판마다 같은 기간 줄이 겹쳤다
 */
async function latestSignals(exec: Exec, id: number, country: string): Promise<Sig[]> {
  for (const d of rows<{ d: string; v: number }>(await exec(OWN_SIGNAL_DATES, [id]))) {
    const 그날판 = rows<{ v: number | null }>(await exec(MAX_SIGNAL_VERSION_ON, [country, d.d]))[0]?.v;
    if (그날판 != null && Number(d.v) === Number(그날판)) return rows<Sig>(await exec(SIGNALS, [id, d.d, country, d.d]));
  }
  return [];
}

/** 매매 기록이 보유 계산보다 새로운가 — 화면(25.806)과 같은 잣대. 모르면 false(말하지 않는다) (25.1206) */
async function 보유늦음(exec: Exec): Promise<boolean> {
  try {
    const [요약, 지금] = await Promise.all([exec(POSITION_SUMMARY_VERSION), exec(TRADES_VERSION)]);
    return positionStale(rows(요약), rows(지금)) === true;
  } catch {
    return false;
  }
}

/** 매매를 넣었는데 보유 계산(재계산 1~2분)이 아직이거나 멈췄을 때 붙이는 줄 (25.1206, 텔레그램 감사) */
export const STALE_NOTE = "※ 매매 반영 전 값입니다 — 보유 재계산이 아직이거나 멈췄습니다(앱 포트폴리오 화면에서 확인)";

async function holdings(exec: Exec): Promise<string> {
  const rs = rows<{ name: string; ticker: string; currency: string; unrealized_pnl: number | null; cost: number; unrealized_pnl_krw: number | null; cost_krw: number; weight_pct: number | null; total: number; d0: string | null; d1: string | null }>(
    await exec(
      // 전체 수·평가 기준일을 함께 읽는다 (25.1126) — 예전 머리 줄은 잘린 줄 수(최대 20)를 보유 수로 적었고, 기준일이 없어
      // 배치가 멈춘 날의 묵은 손익을 오늘 값처럼 보였다(CLAUDE.md 기준 시각 표시)
      `SELECT COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.ticker, p.currency, p.unrealized_pnl, p.cost,
       p.unrealized_pnl_krw, p.cost_krw, p.weight_pct,
       (SELECT COUNT(*) FROM positions) AS total, (SELECT MIN(price_date) FROM positions) AS d0,
       (SELECT MAX(price_date) FROM positions) AS d1
       FROM positions p JOIN stocks s ON s.id = p.stock_id
       ORDER BY CASE WHEN p.cost_krw > 0 THEN p.unrealized_pnl_krw * 1.0 / p.cost_krw END DESC NULLS LAST LIMIT ${HOLDINGS_LIMIT}`,
    ),
  );
  if (rs.length === 0) return "보유 종목이 없습니다";
  const { total, d0, d1 } = rs[0];
  const 기준 = d0 && d1 ? ` · 평가 ${d0 === d1 ? d1 : `${d0}~${d1}`} 종가` : "";
  const 머리 = total > rs.length ? `보유 ${total}종목 중 손익률 상위 ${rs.length}` : `보유 ${total}종목 (손익률 순)`;
  const 늦음 = await 보유늦음(exec);
  return [
    `${머리}${기준}`,
    ...(늦음 ? [STALE_NOTE] : []),
    ...rs.map((r) => {
      const 률 = r.unrealized_pnl_krw != null && r.cost_krw > 0 ? `${n((r.unrealized_pnl_krw / r.cost_krw) * 100, 1)}%` : "평가 없음";
      // 해외는 주가만의 손익률도 (원화 손익률에는 환율이 섞인다, 25.1126)
      const 주가 = r.currency !== "KRW" && r.unrealized_pnl != null && r.cost > 0 ? ` (주가 ${n((r.unrealized_pnl / r.cost) * 100, 1)}%)` : "";
      return `· ${r.name} ${률}${주가}${r.weight_pct != null ? ` · 비중 ${n(r.weight_pct, 1)}%` : ""}`;
    }),
  ].join("\n");
}

async function alerts(exec: Exec): Promise<string> {
  // **시장마다 제 마지막 거래일** (docs/infra.md 25.1253). 예전에는 표 전체의 MAX(trade_date) 하나로 걸러, 한국 낮에 물으면 미국의 지난 밤
  // 정규장(현지 날짜가 하루 앞) 알림이 통째로 빠졌다. 두 시장을 따로 묶어 날짜를 붙인다
  const rs = rows<{ name: string; message: string; trade_date: string; market: string }>(
    await exec(
      `SELECT COALESCE(s.name_ko, s.name_en, s.ticker) AS name, a.message, a.trade_date, a.market FROM alerts a
       JOIN (SELECT market, MAX(trade_date) AS d FROM alerts GROUP BY market) m ON m.market = a.market AND m.d = a.trade_date
       JOIN stocks s ON s.id = a.stock_id
       ORDER BY a.market, a.created_at DESC LIMIT 40`,
    ),
  );
  if (rs.length === 0) return "장중 알림 기록이 없습니다";
  const 시장들 = [...new Set(rs.map((r) => r.market))];
  return 시장들
    .map((m) => {
      const 줄 = rs.filter((r) => r.market === m).slice(0, 20);
      const 이름 = m === "KR" ? "국내" : m === "US" ? "미국" : m;
      return [`장중 알림 ${이름} (${줄[0].trade_date}, 최근 ${줄.length}건)`, ...줄.map((r) => `· ${r.name}: ${r.message}`)].join("\n");
    })
    .join("\n\n");
}

/**
 * 웹훅을 걸 출처 — **운영 대표 주소**가 있으면 그것 (docs/infra.md 25.1128, 텔레그램 감사 재현). 예전에는 요청이 들어온 출처라
 * 대시보드 "Visit" 로 연 배포 고유 주소(`프로젝트-해시-팀.vercel.app`, 이것도 VERCEL_ENV=production)에서 누르면 웹훅이 **그 배포 하나에**
 * 묶여, 뒤 배포의 수정이 반영되지 않고 배포 보호가 켜져 있으면 봇이 말없이 답하지 않았다(그래도 "켰습니다"). Vercel 이 주는
 * `VERCEL_PROJECT_PRODUCTION_URL`(호스트만, 시스템 환경변수 — 노출 설정이 꺼져 있으면 비어 `[확인필요]`)이 없으면 예전처럼 요청 출처
 */
export function webhookOrigin(requestUrl: string, productionHost: string | undefined): string {
  const host = productionHost?.trim().replace(/^https?:\/\//, "").replace(/\/+$/, "");
  return host ? `https://${host}` : new URL(requestUrl).origin;
}

/** 웹훅이 답을 기다리는 상한(ms). Vercel 무료 함수 상한(10초 `[확인필요: 플랜별]`)보다 짧게 — 넘으면 "DB 응답 없음" 을 보내고 200 (25.1133) */
export const ANSWER_TIMEOUT_MS = 8_000;

/** 약속이 상한 안에 끝나지 않으면 오류로 끝낸다. 늦게 끝난 원래 약속의 결과는 버린다 */
export function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const 늦음 = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`DB 가 ${Math.round(ms / 1000)}초 안에 답하지 않았습니다`)), ms);
  });
  return Promise.race([p, 늦음]).finally(() => clearTimeout(timer));
}
