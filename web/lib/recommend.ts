/**
 * 추천 종목 조회.
 *
 * 스크리너와 다른 화면이다. **스크리너는 내가 조건을 정하고, 여기는
 * 시스템이 고른 결과를 본다.** 화면에서 조정할 것이 없다.
 *
 * 규칙은 docs/signals.md, 계산은 batch/services/signals.py 가 한다.
 * 웹은 계산하지 않는다. 저장된 값을 읽어 보여주기만 한다(CLAUDE.md).
 *
 * 화면 구조는 docs/design.md 3.9 절의 **1부 개별 종목**과 같은 규칙이다. 다만 텔레그램 리포트 1부는 시장별 상위
 * `REPORT_TOP_STOCKS` 개만 싣는다 — 이 화면은 신호 전부를 보여 주므로 그 밖의 카드는 리포트 1부·2부 어디에도 없다 (25.586).
 * 포트폴리오 제약을 보지 않고 종목 자체를 본다. 기간별로 나눠 보여 주고
 * 섞지 않는다. 단기·중기·장기는 판단 근거가 다르기 때문이다.
 */

import { z } from "zod";
import { localDate } from "@/lib/market";
import { MARKETS_BY_COUNTRY } from "@/lib/screener";

const ALL_MARKET_CODES = [...new Set([...MARKETS_BY_COUNTRY.KR, ...MARKETS_BY_COUNTRY.US].map(([code]) => code))];

export const HORIZON_LABEL: Record<string, string> = {
  short: "단기 (1~3개월)",
  mid: "중기 (3~12개월)",
  long: "장기 (1년 이상)",
};

/** "단기"·"중기"·"장기". 모르는 값은 그대로 — 리포트 기록의 날 키("short")가 화면에 나오지 않게 (25.927) */
export function horizonShort(h: unknown): string {
  const k = String(h ?? "");
  return HORIZON_LABEL[k]?.split(" ")[0] ?? k;
}

export const HORIZON_ORDER = ["short", "mid", "long"] as const;

export const recommendQuerySchema = z.object({
  /** 국내·미국은 탭으로 나눠 따로 본다. 기준일도 나라마다 따로 잡는다 */
  country: z.enum(["KR", "US"]).default("KR"),
  // 시장은 **그 나라의 시장만** (docs/infra.md 25.315). 예전에는 KOSPI·KOSDAQ 만 받고 나라와 대 보지 않아
  // `{country:"US", market:"KOSPI"}` 가 0건을 "조건을 만족한 종목이 없습니다 — 오류가 아닙니다" 로 안내했고,
  // 미국 시장(NASDAQ 등)은 400 이었다. 목록은 스크리너와 같은 정의처(`MARKETS_BY_COUNTRY`)에서 뽑는다
  market: z.enum(["ALL", ...ALL_MARKET_CODES] as [string, ...string[]]).default("ALL"),
  horizon: z.enum(["ALL", "short", "mid", "long"]).default("ALL"),
  // **기본으로 다 보인다** (docs/infra.md 25.377). 예전 기본 30 은 화면이 늘 그대로 써, 신호가 40건인 날 종합 점수
  // 하위 10건이 **말없이** 사라졌다 — 건수·기간 탭도 잘린 수로 셌다. 한 나라 신호는 수십 건이라 500 이면 넉넉하다
  limit: z.number().int().min(1).max(500).default(500),
}).refine(
  (q) => q.market === "ALL"
    || (MARKETS_BY_COUNTRY[q.country] as ReadonlyArray<readonly [string, string]>).some(([v]) => v === q.market),
  { message: "그 나라에 없는 시장입니다", path: ["market"] },
);

export type RecommendQuery = z.infer<typeof recommendQuerySchema>;

export interface Tranche {
  step: number;
  ratio: number;
  price: number;
  amount: number | null;
}

export interface RecommendRow {
  stock_id: number;
  ticker: string;
  name: string;
  market: string;
  sector: string | null;
  currency: string;

  horizon: string;
  signal_type: string;
  buy_zone_low: number;
  buy_zone_high: number;
  target_price: number | null;
  stop_price: number | null;

  suggested_weight_pct: number | null;
  suggested_amount: number | null;
  size_reduction: number;
  sector_cap_applied: number;
  sector_cap_note: string | null;

  rationale_text: string;
  rationale_data: string | null;
  tranche_plan: string;

  total_score: number | null;
  factor_scores: string | null;
  /** 센티먼트 축(−100~+100). 종합 점수에 들어간 값. 25.379 */
  sentiment_score?: number | null;
  /** 종합 점수에 들어간 센티먼트 가중치(%). 0 이면 반영 안 됨. 25.379 */
  sentiment_weight_used?: number | null;
  rank_in_market: number | null;
  /** 종합 점수의 기준일(scores.as_of_date). 점수가 없으면 null (25.911) */
  score_date?: string | null;
  as_of_date: string;
  close: number | null;
  price_date: string | null;
}

/**
 * 그 나라의 가장 최근 계산분만 본다.
 *
 * 신호는 날마다 쌓이므로 기준일을 고정하지 않으면 과거 신호가 섞인다.
 * 국내와 미국은 배치 시각이 달라 기준일도 다르다. 전체 MAX 로 잡으면 한쪽이
 * 통째로 사라진다. 어느 날짜의 결과인지 화면에 반드시 표시한다.
 */
/**
 * **그 나라의 마지막 계산일** (docs/infra.md 25.145).
 *
 * 전에는 `signals` 의 MAX 만 봤다. 그러면 **아무 신호도 안 걸린 날**에는 그 표에 새 행이
 * 안 생기므로 기준일이 어제로 남고, 화면은 **어제 신호를 오늘 것처럼** 보여 준다.
 * "오늘은 0건" 과 "배치가 안 돌았다" 가 화면에서 똑같아진다.
 *
 * `signal_checks` 는 **신호가 없어도 전 종목에 남는다**(`jobs/signals`: "판정표는 신호와
 * 별개로 전 종목에 남긴다"). 그래서 둘의 MAX 가 곧 **마지막으로 계산한 날**이다.
 */
export const LAST_CALC_DATE = `SELECT MAX(d) AS d FROM (
  SELECT MAX(sg.as_of_date) AS d FROM signals sg JOIN stocks s ON s.id = sg.stock_id WHERE s.country = ?
  UNION ALL
  SELECT MAX(ck.as_of_date) AS d FROM signal_checks ck JOIN stocks s ON s.id = ck.stock_id WHERE s.country = ?
)`;

/** 판정표 표가 아직 없는 DB(0034 마이그레이션 전)에서 되돌아갈 옛 잣대 */
export const LAST_CALC_DATE_FALLBACK = `SELECT MAX(sg.as_of_date) AS d FROM signals sg
JOIN stocks s ON s.id = sg.stock_id WHERE s.country = ?`;

export function buildQuery(query: RecommendQuery, asOf: string): {
  sql: string;
  args: Array<string | number>;
} {
  const where: string[] = [
    "s.country = ?",
    // **기준일은 부르는 쪽이 정해 넘긴다** (docs/infra.md 25.145). 전에는 여기서
    // `signals` 의 MAX 를 직접 박았다 — 한 건도 안 걸린 날에는 그 표에 새 행이 없어
    // 어제 신호가 딸려 나왔다. 이제 `LAST_CALC_DATE` 가 정한 날만 본다
    "sg.as_of_date = ?",
    // **계산 판까지 봐야 한 행이다** (docs/infra.md 25.378). `signals` 의 유니크 키에 `calc_version` 이 있고 적재는
    // 같은 판만 지운다 — 판을 올린 뒤 같은 기준일을 다시 계산하면 한 종목·기간 카드가 두 장 나왔다(점수는 25.91 에서 고쳤다)
    // 25.423: (종목·기간)마다가 아니라 **그 나라·그 기준일의 가장 새 판 하나**다. 판마다 고르면 새 판에서 사라진
    // 기간·종목의 옛 판 행이 살아남았다(교차검증 반박). 배치 리포트·장중 감시와 같은 조건이다
    // 바깥 행을 가리키지 않고 인자로 받는다 — 가리키면 신호 행마다 다시 돌아 읽는 행이 수백 배가 됐다 (25.428)
    "sg.calc_version = (SELECT MAX(c.calc_version) FROM signals c JOIN stocks s3 ON s3.id = c.stock_id"
      + " WHERE s3.country = ? AND c.as_of_date = ?)",
    "s.status = 'active'",
  ];
  const args: Array<string | number> = [query.country, asOf, query.country, asOf];

  if (query.market !== "ALL") {
    where.push("s.market = ?");
    args.push(query.market);
  }
  if (query.horizon !== "ALL") {
    where.push("sg.horizon = ?");
    args.push(query.horizon);
  }

  const sql = `
SELECT
  s.id AS stock_id, s.ticker, COALESCE(s.name_ko, s.name_en) AS name,
  s.market, s.sector, sg.currency,
  sg.horizon, sg.signal_type, sg.buy_zone_low, sg.buy_zone_high,
  sg.target_price, sg.stop_price,
  sg.suggested_weight_pct, sg.suggested_amount, sg.size_reduction,
  sg.sector_cap_applied, sg.sector_cap_note,
  sg.rationale_text, sg.rationale_data, sg.tranche_plan, sg.as_of_date,
  sc.total_score, sc.factor_scores, sc.rank_in_market,
  -- 카드의 종합·순위·팩터·센티먼트가 **어느 날 점수인지** (docs/infra.md 25.911, 감사). 근거표에 행이 없어 날짜를 알 수 없었다
  sc.as_of_date AS score_date,
  -- 센티먼트 축과 종합 점수에 실제로 들어간 가중치 (docs/infra.md 25.379). 5팩터와 **따로** 보인다
  sc.sentiment_score, sc.sentiment_weight_used,
  p.close, p.date AS price_date
FROM signals sg
JOIN stocks s ON s.id = sg.stock_id
-- **계산 판(calc_version)까지 봐야 한 행이다** (2026-09-21, docs/infra.md 25.91).
-- scores 의 유니크 키는 (stock_id, as_of_date, calc_version) 이고 적재는 같은 판만
-- 지운다. 계산식을 올린 날에는 같은 기준일에 두 행이 남아 **추천 목록에 같은 종목이
-- 두 번** 나오고, 둘의 총점이 다르다.
-- (이 주석에 백틱을 쓰면 템플릿 리터럴이 닫혀 파일이 깨진다 — 2026-09-21에 겪었다)
LEFT JOIN scores sc
  ON sc.stock_id = s.id
-- **신호 기준일 이하의 점수**다 (docs/infra.md 25.269). 예전에는 가장 새 점수라, 신호 계산이 실패한 날
-- 기준일(어제 신호) 아래에 오늘 점수가 섞였고 텔레그램 리포트(기준일 이하)와 종합 점수가 달랐다
 AND sc.as_of_date = (SELECT MAX(as_of_date) FROM scores WHERE stock_id = s.id AND as_of_date <= sg.as_of_date)
 AND sc.calc_version = (SELECT MAX(c.calc_version) FROM scores c
                        WHERE c.stock_id = sc.stock_id AND c.as_of_date = sc.as_of_date)
-- **종목마다 인덱스로 한 행만 찾는다** (docs/infra.md 25.586, 감사). 예전에는 WITH 절에서 prices 를 종목별로 묶어
-- 화면을 열 때마다 가격 표 전체(두 나라 수백만 행)를 훑었다 — 첫 화면이라 새로고침마다 D1 하루 읽기 한도를 크게 썼다
LEFT JOIN prices p ON p.stock_id = s.id
 AND p.date = (SELECT MAX(p2.date) FROM prices p2 WHERE p2.stock_id = s.id)
WHERE ${where.join(" AND ")}
ORDER BY sc.total_score DESC NULLS LAST, sg.suggested_weight_pct DESC NULLS LAST
LIMIT ?`;

  // 한 건 더 읽어 **잘렸는지** 안다 — 잘렸으면 경로가 말한다 (25.377)
  return { sql, args: [...args, query.limit + 1] };
}

/** 분할 계획을 안전하게 푼다. 깨져 있으면 빈 목록으로 둔다. */
export function parseTranches(raw: string | null): Tranche[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/** 팩터 점수를 푼다. 없는 팩터는 null 로 남긴다. */
export function parseFactors(raw: string | null): Record<string, number | null> {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

/**
 * 비중이 줄어든 이유를 한 줄로.
 *
 * "왜 이 종목이 추천인데 금액이 작은가" 에 답한다. 답하지 않으면
 * 사용자는 추천 자체를 의심하게 된다.
 */
export function reductionNote(reduction: number, rationaleData: string | null = null): string | null {
  if (reduction >= 0.999) return null;
  // **배치가 쓴 한 줄을 그대로 보인다** (docs/infra.md 25.624). 웹은 계산하지 않는다 — 예전에는 여기서 (1−배수)×100 을 내고
  // min 에서 진 요인까지 까닭으로 적었다. 아래 옛 방식은 그 줄이 없는 **옛 행**만 쓴다(다음 신호 계산에서 사라진다)
  try {
    const v = rationaleData ? (JSON.parse(rationaleData) as Record<string, unknown>) : null;
    if (v && typeof v.reduction_note === "string") return v.reduction_note;
    // **배치가 "적을 것 없음"(null) 이라 했으면 그대로 없다** (docs/infra.md 25.689, 교차검증). 1% 미만 축소를 배치가
    // null 로 두자(25.687) 이 줄이 옛 방식으로 떨어져 웹 카드에만 "0% 줄였습니다" 가 남았다. 키가 없는 옛 행만 아래로 간다
    if (v && "reduction_note" in v && v.reduction_note === null) return null;
  } catch {
    // 못 읽으면 아래 옛 방식
  }
  const cut = Math.round((1 - reduction) * 100);
  // **줄인 까닭을 실제 배수에서 읽는다** (docs/infra.md 25.233). 배치의 축소 배수는
  // min(변동성, MDD) × 시장 국면 이다(batch/services/signals.py `size_weight`). 예전 글은 늘
  // "변동성·낙폭이 커서" 라고 해서, 지수가 200일선 아래라 ×0.5 가 된 종목(변동성·MDD ×1.00)에도
  // 종목 탓을 했다 — 같은 카드의 근거표와 말이 달랐다
  let parsed: Record<string, unknown> = {};
  try {
    const v = rationaleData ? JSON.parse(rationaleData) : null;
    if (v && typeof v === "object") parsed = v as Record<string, unknown>;
  } catch {
    // 근거를 못 읽으면 까닭을 지어내지 않는다 — 아래에서 "위험 기준" 으로만 말한다
  }
  const 줄었나 = (key: string) => typeof parsed[key] === "number" && (parsed[key] as number) < 0.999;
  const 까닭 = [
    줄었나("volatility_factor") ? "변동성" : null,
    줄었나("mdd_factor") ? "낙폭" : null,
    줄었나("regime_factor") ? "시장 약세(지수 200일선 아래)" : null,
  ].filter((x): x is string => x !== null);
  const 앞 = 까닭.length ? `${까닭.join("·")} 때문에` : "위험 기준에 따라";
  return `${앞} 비중을 ${cut}% 줄였습니다`;
}

/** 기간별로 묶는다. 판단 근거가 다르므로 섞지 않는다. */
export function groupByHorizon(rows: RecommendRow[]): Array<{
  horizon: string;
  label: string;
  rows: RecommendRow[];
}> {
  return HORIZON_ORDER.map((horizon) => ({
    horizon,
    label: HORIZON_LABEL[horizon],
    rows: rows.filter((r) => r.horizon === horizon),
  })).filter((group) => group.rows.length > 0);
}

/**
 * 근거표의 한 행.
 *
 * 배치(batch/services/signals.py)가 만들어 signals.rationale_data.criteria 에
 * 저장한 것을 그대로 그린다. **웹은 문턱값을 모른다.** 규칙의 정의처는 파이썬
 * 하나여야 하고, 여기에 복사하면 둘이 어긋난다.
 */
export interface Criterion {
  label: string;
  display: string;
  threshold: string;
  source: string;
  as_of: string | null;
  /**
   * true 통과 · false 탈락 · null 참고(판정에 쓰지 않은 행).
   * 위성 ETF 의 "상위 보유 비중" "분배금 처리" 같은 행이 null 이다(docs/etf.md 10.4).
   */
  passed: boolean | null;
}

/**
 * 근거표를 푼다. 없거나 깨져 있으면 빈 목록이다.
 *
 * 빈 목록이면 화면은 "근거 보기" 를 아예 그리지 않는다. "-" 로 채운 표는
 * 확인이 아니라 장식이다.
 */
/** 판정 열에 쓰는 글자. 색만으로 뜻을 전하지 않는다(색을 못 보는 경우). */
export function criterionStatus(passed: boolean | null | undefined): { text: string; tone: "ok" | "fail" | "info" } {
  if (passed === true) return { text: "통과", tone: "ok" };
  if (passed === false) return { text: "탈락", tone: "fail" };
  return { text: "참고", tone: "info" };
}

export function parseCriteria(raw: string | null): Criterion[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    const list = parsed?.criteria;
    if (!Array.isArray(list)) return [];
    return list.filter(
      (c) =>
        c &&
        typeof c.label === "string" &&
        typeof c.display === "string" &&
        typeof c.threshold === "string" &&
        typeof c.source === "string",
    );
  } catch {
    return [];
  }
}

/**
 * 기준일이 오늘보다 며칠 지났나. YYYY-MM-DD 가 아니면(예: "2025 사업보고서") null.
 *
 * 웹은 계산하지 않는다는 규칙이 있지만, 이건 투자 수치가 아니라 날짜 차이다.
 * 화면이 "이 값이 얼마나 오래됐나" 를 보여주는 표시 논리다.
 */
export function daysSince(asOf: string | null, today: Date = new Date()): number | null {
  if (!asOf || !/^\d{4}-\d{2}-\d{2}$/.test(asOf)) return null;
  const then = new Date(`${asOf}T00:00:00Z`);
  if (Number.isNaN(then.getTime())) return null;
  // **"오늘" 은 사용자의 날짜(KST)다** (docs/infra.md 25.236). 예전에는 UTC 날짜로 세서 00~09시 KST 에는 하루 짧았고,
  // 같은 값을 `/status`(`health.freshnessVerdict`, KST)와 배치(`cal.user_today`)는 하루 더 오래됐다고 말했다
  const now = new Date(`${localDate("KR", today)}T00:00:00Z`).getTime();
  return Math.floor((now - then.getTime()) / 86_400_000);
}

/**
 * 오래된 값으로 볼 문턱(일). **화면 전체가 이 하나를 쓴다.**
 *
 * 2026-09-21 까지 이 주석은 "스코어·신호 배치는 지금 주 1회다" 라고 적고 있었다.
 * **그 전제가 없어진 지 오래다** — Step 9 에서 둘 다 일일 배치 안으로 들어왔다
 * (`batch/jobs/daily.py` 머리말). 주석이 낡은 채로 남아 값의 근거가 사라졌고,
 * 그 사이 종목 상세 화면은 **4 를 손으로 박아** 쓰고 있었다(docs/infra.md 25.94).
 *
 * 이제 이 값을 정하는 것은 배치 주기가 아니라 **휴장이 얼마나 길어질 수 있는가** 다.
 * 배치는 거래일마다 돈다. 그러니 마지막 기준일이 오래된 것은 배치가 죽어서가 아니라
 * **장이 안 열려서**일 수 있다.
 *
 * 실측 (2016-01-01~2026-12-31, `exchange_calendars`):
 *
 * | 거래소 | 거래일 사이 최장 간격 | 언제 |
 * |---|---|---|
 * | XNYS | **4일** | 2016-01-15 → 01-19 (마틴 루터 킹 데이) |
 * | XKRX | **11일** | 2017-09-29 → 10-10 (추석 + 임시공휴일 + 개천절) |
 *
 * **값 나이의 최댓값은 `간격 − 1` 이 아니다** (docs/infra.md 25.242 에서 바로잡음). 기준일은 그날 아침 배치가 넘기는
 * **직전 거래일**이라 한 세션 늦다. 연속한 세 거래일 Z·A·B 에서 A 아침부터 B 아침 배치 전까지 기준일은 Z 이고,
 * 그 끝의 나이는 `B − Z` — **연속 세 거래일의 폭**이다. XKRX 는 2017-09-29 → 10-11 의 **12일**(XNYS 5일).
 * 예전 값 10 은 그 연휴에 정상 배치에도 이틀 넘게 노란 줄을 띄웠다.
 * `tests/test_stale_threshold.py` 가 이 수치를 달력에서 **다시 재어** 대조한다 —
 * 어림이 아니라 실측이고, 달력이 바뀌면 테스트가 알려 준다.
 *
 * 한국 기준이라 미국에는 느슨하다. 나라별로 가르지 않은 이유: 한 화면에 두 시장이
 * 섞여 나오고(추천 목록), **늑대야 하고 외치는 쪽이 더 나쁘다** — 노란 줄이 예사가
 * 되면 진짜로 멈춘 날에도 아무도 안 본다. 배치가 정말 멈춘 것은 `/status` 화면과
 * 무응답 알림이 잡는다.
 */
export const STALE_AFTER_DAYS = 12;

export function isStale(asOf: string | null, today: Date = new Date()): boolean {
  const days = daysSince(asOf, today);
  return days !== null && days > STALE_AFTER_DAYS;
}

/** 마지막 배치 시각을 KST 로. ISO 문자열이 아니면 그대로 돌려준다. */
export function formatKst(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}


// ----------------------------------------------------------------------
// 어제와 비교 (Step 24, 2026-09-17). 매일 여는 화면에서 "오늘 뭐가 달라졌나" 가 첫 질문이다
// ----------------------------------------------------------------------

/** 최근 기준일 바로 전의 기준일. 신호가 날마다 쌓이므로 그 전 계산분과 견준다 */
/**
 * 비교할 **앞선 계산일** — `LAST_CALC_DATE` 와 같은 잣대(signals ∪ signal_checks)다 (docs/infra.md 25.384).
 * 예전에는 `signals` 만 봐서 신호가 0건이었던 계산일을 건너뛰고 그 전날과 견줬다 — 0건 뒤 다시 걸린 종목에
 * "새로" 가 붙지 않았다(25.145 와 같은 모양). 인자는 (나라, 기준일, 나라, 기준일)
 */
export const PREVIOUS_AS_OF = `SELECT MAX(d) AS d FROM (
  SELECT MAX(sg.as_of_date) AS d FROM signals sg JOIN stocks s ON s.id = sg.stock_id WHERE s.country = ? AND sg.as_of_date < ?
  UNION ALL
  SELECT MAX(ck.as_of_date) AS d FROM signal_checks ck JOIN stocks s ON s.id = ck.stock_id WHERE s.country = ? AND ck.as_of_date < ?
)`;

/** 판정표가 아직 없는 DB(0034 전)에서 되돌아갈 옛 잣대 */
export const PREVIOUS_AS_OF_FALLBACK = `SELECT MAX(sg.as_of_date) AS d FROM signals sg JOIN stocks s ON s.id = sg.stock_id
WHERE s.country = ? AND sg.as_of_date < ?`;

/**
 * 그 기준일의 신호 (종목·기간). 빠진 것을 이름으로 보여 주려고 이름도 읽는다.
 * 그 나라·그 기준일의 가장 새 판 하나만 (docs/infra.md 25.423). 인자는 `signalsOnArgs` 로 만든다(25.428)
 */
/**
 * 오늘·전 기준일 신호 집합("새로 N건"·"빠짐"). **카드 질의와 같은 상태 조건**이다 (docs/infra.md 25.802, 추천 감사 #5) — 예전에는 카드만
 * `status = 'active'` 라 제외·폐지된 종목의 신호가 "새로 N건" 에는 세지고 카드와 NEW 표시는 없었다
 */
export const SIGNALS_ON = `SELECT DISTINCT sg.stock_id, sg.horizon, s.ticker, COALESCE(s.name_ko, s.name_en) AS name
FROM signals sg JOIN stocks s ON s.id = sg.stock_id WHERE s.country = ? AND sg.as_of_date = ? AND s.status = 'active'
  AND sg.calc_version = (SELECT MAX(c.calc_version) FROM signals c JOIN stocks s3 ON s3.id = c.stock_id
    WHERE s3.country = ? AND c.as_of_date = ?)`;

/** `SIGNALS_ON` 의 인자. 판 하위질의가 바깥 행을 가리키지 않게 나라·날짜를 한 번 더 받는다 (docs/infra.md 25.428) */
export function signalsOnArgs(country: string, asOf: string): string[] {
  return [country, asOf, country, asOf];
}

export interface SignalKeyRow {
  stock_id: number;
  horizon: string;
  ticker: string;
  name: string;
}

export function signalKey(row: Pick<SignalKeyRow, "stock_id" | "horizon">): string {
  return `${row.stock_id}:${row.horizon}`;
}

/**
 * 오늘 신호와 전 기준일 신호의 차이. 새로 들어온 것은 키로, 빠진 것은 이름으로 돌려준다.
 * 계산이 아니라 집합 비교다. 전 기준일이 없으면(첫 계산) 둘 다 빈 것이고 화면은 비교 줄을 그리지 않는다.
 */
export function diffSignals(
  today: Array<Pick<SignalKeyRow, "stock_id" | "horizon">>,
  previous: SignalKeyRow[],
): { added: string[]; dropped: SignalKeyRow[] } {
  const before = new Set(previous.map(signalKey));
  const now = new Set(today.map(signalKey));
  return {
    added: today.map(signalKey).filter((k) => !before.has(k)),
    dropped: previous.filter((r) => !now.has(signalKey(r))),
  };
}


// ----------------------------------------------------------------------
// 신호 성적표 (docs/signals.md 10장). 배치가 낸 집계를 읽기만 한다
// ----------------------------------------------------------------------

export const OUTCOME_STATS = `SELECT horizon, window_days, n, avg_ret, win_rate, avg_excess, hit_target_rate, hit_stop_rate, since, computed_at
FROM signal_outcome_stats WHERE country = ? ORDER BY CASE horizon WHEN 'short' THEN 0 WHEN 'mid' THEN 1 ELSE 2 END, window_days`;

export interface OutcomeStat {
  horizon: string;
  window_days: number;
  n: number;
  avg_ret: number | null;
  win_rate: number | null;
  avg_excess: number | null;
  hit_target_rate: number | null;
  hit_stop_rate: number | null;
  since: string | null;
  computed_at: string;
}

/**
 * 점수 보정표 (docs/signals.md 10.1, docs/infra.md 25.949) — 성적표 작업이 `batch_runs.step_log.calibration` 에 남긴 것을
 * 읽기만 한다. 표가 따로 없다(마이그레이션은 사용자 결정 몫). 마지막 **성공한** 실행 하나.
 */
export const LATEST_OUTCOME_RUN = `SELECT step_log, finished_at FROM batch_runs
WHERE job_name = 'signal_outcomes' AND market = ? AND status = 'success' ORDER BY id DESC LIMIT 1`;

export interface CalibrationBucket {
  lo: number;
  hi: number;
  n: number;
  avg_ret: number | null;
  win_rate: number | null;
}

export interface Calibration {
  window: number;
  n: number;
  rho: number | null;
  verdict: string;
  buckets: CalibrationBucket[];
}

/** `step_log` 글에서 보정표를 꺼낸다. 없거나 깨졌으면 null — 지어내지 않는다. 적어 두기 전 실행이면 null 이다 */
export function parseCalibration(stepLog: string | null | undefined): Calibration[] | null {
  if (!stepLog) return null;
  try {
    const log = JSON.parse(stepLog) as { calibration?: unknown };
    if (!Array.isArray(log.calibration)) return null;
    const out: Calibration[] = [];
    for (const c of log.calibration) {
      if (!c || typeof c !== "object") return null;
      const r = c as Record<string, unknown>;
      if (typeof r.window !== "number" || typeof r.n !== "number" || typeof r.verdict !== "string") return null;
      const buckets = Array.isArray(r.buckets) ? (r.buckets as CalibrationBucket[]) : [];
      out.push({ window: r.window, n: r.n, rho: typeof r.rho === "number" ? r.rho : null, verdict: r.verdict, buckets });
    }
    return out;
  } catch {
    return null;
  }
}

/** 배치 출력(`calibration.render_lines`)과 같은 줄 — 첫 줄이 판정, 다음이 구간 */
export function calibrationLines(c: Calibration): string[] {
  const lines = [`${c.window}일 뒤: ${c.verdict}`];
  for (const b of c.buckets) {
    if (b.avg_ret === null || b.win_rate === null) lines.push(`${b.lo}~${b.hi}점: 표본 ${b.n}건 — 평균 안 냄`);
    else lines.push(`${b.lo}~${b.hi}점: 평균 ${부호(b.avg_ret, "%")} · 이긴 ${rateText(b.win_rate)} (n=${b.n})`);
  }
  return lines;
}

/** 부호 붙은 소수 한 자리. 반올림해 0 이면 부호 없이 "0.0" — "-0.0" 을 쓰지 않는다 (25.699) */
function 부호(v: number, 단위: string): string {
  const 보이는값 = Number((v * 100).toFixed(1));
  return `${보이는값 > 0 ? "+" : ""}${(보이는값 === 0 ? 0 : 보이는값).toFixed(1)}${단위}`;
}

/**
 * 비율 퍼센트. **반올림으로 끝값이 되지 않게** 한다 (docs/infra.md 25.699, 감사) — 199/200 이 "100%" 로 나와
 * 한 번도 안 진 것처럼 읽혔고, 0.4% 가 "0%" 였다.
 */
export function rateText(v: number): string {
  if (v > 0 && v < 0.005) return "1% 미만";
  if (v >= 0.995 && v < 1) return "99% 넘게";
  return `${(v * 100).toFixed(0)}%`;
}

/** 성적표에서 "단정하지 않는" 표본 하한. 복기(`docs/review.md` MIN_SAMPLE)와 같은 값 (25.699) */
export const OUTCOME_CAUTION_N = 10;

/** "단기 20일 뒤 평균 +3.2% · 이긴 58% · 지수 대비 +1.0%p (n=31)" 한 줄. 표본이 모자라면 그 사실을 적는다 */
export function outcomeLine(s: OutcomeStat): string {
  const label = horizonShort(s.horizon);
  if (s.avg_ret === null || s.win_rate === null) return `${label} ${s.window_days}일: 표본 ${s.n}건 — 아직 평균을 내지 않음`;
  const parts = [`평균 ${부호(s.avg_ret, "%")}`, `이긴 ${rateText(s.win_rate)}`];
  if (s.avg_excess !== null) parts.push(`지수 대비 ${부호(s.avg_excess, "%p")}`);
  if (s.hit_target_rate !== null) parts.push(`목표 도달 ${rateText(s.hit_target_rate)}`);
  if (s.hit_stop_rate !== null) parts.push(`손절 터치 ${rateText(s.hit_stop_rate)}`);
  // 배치는 5건부터 평균을 낸다. 10건 미만은 "단정하지 않는다" 를 붙인다 — 복기 화면과 같은 기준 (25.699, 감사)
  const 주의 = s.n < OUTCOME_CAUTION_N ? " — 표본이 적어 단정하지 않음" : "";
  return `${label} ${s.window_days}일 뒤: ${parts.join(" · ")} (n=${s.n}${주의})`;
}

/**
 * 추천 카드의 센티먼트 줄 (docs/infra.md 25.379, CLAUDE.md "화면에는 항상 분리 표시").
 * 예전에는 카드가 5팩터만 그리고 센티먼트는 아예 없었다 — 센티먼트 +60 이 가중치 10% 로 종합 점수를 3점쯤 올려도
 * 카드에는 "종합 72" 만 보였다. 5팩터에 섞지 않고 따로 적는다.
 */
export function sentimentLine(row: Pick<RecommendRow, "sentiment_score" | "sentiment_weight_used">): string {
  const w = row.sentiment_weight_used;
  if (row.sentiment_score === null || row.sentiment_score === undefined) {
    // 배치는 값이 없을 때 쓴 가중치를 0 으로 저장해 w 로는 까닭을 가를 수 없다 (25.833)
    return "센티먼트 반영 안 됨 (값이 없었거나 가중치 0)";
  }
  const v = Math.round(row.sentiment_score);
  const 부호 = v > 0 ? "+" : "";
  return w ? `센티먼트 ${부호}${v} (종합 점수에 ${w}% 반영)` : `센티먼트 ${부호}${v} (종합 점수에 반영 안 됨)`;
}

/** 텔레그램 리포트 1부에 싣는 시장별 종목 수. `batch/services/report_picks.TOP_STOCKS` 와 같아야 한다(테스트가 맞춰 본다, 25.586) */
export const REPORT_TOP_STOCKS = 5;


/** 그 시장의 가장 최근 신호 실행 — 상태와 상관없이 (docs/infra.md 25.798) */
export const LATEST_SIGNAL_RUN = `SELECT status, started_at, trade_date, error_text, step_log FROM batch_runs
WHERE job_name = 'signals' AND market = ? ORDER BY started_at DESC LIMIT 1`;

/** 그 나라의 마지막 **성공한** 신호 실행 시각 — `signalRunNote` 의 둘째 인자 (추천 화면·종목 상세가 같이 쓴다, 25.807) */
export const LAST_SIGNAL_SUCCESS = `SELECT finished_at, market FROM batch_runs
WHERE job_name = 'signals' AND status = 'success' AND market = ? ORDER BY finished_at DESC LIMIT 1`;

export interface SignalRunRow {
  status: string;
  started_at: string | null;
  trade_date: string | null;
  error_text: string | null;
  step_log: string | null;
}

/**
 * 가장 최근 신호 실행이 **건너뜀·실패**이고 마지막 성공보다 뒤면, 화면의 카드가 그 전 기준일 것이라고 말한다 (25.798, 추천 감사 #4).
 * 까닭은 배치가 적은 `step_log.reason`·`error_text` 를 그대로 옮긴다 — 짐작하지 않는다
 */
export function signalRunNote(run: SignalRunRow | undefined, lastSuccessFinished: string | null, asOf: string | null): string | null {
  if (!run || (run.status !== "skipped" && run.status !== "failed")) return null;
  if (lastSuccessFinished && run.started_at && run.started_at <= lastSuccessFinished) return null;
  // **보이는 기준일보다 새 날짜를 돌리다 멈춘 것만** (25.801, 교차검증) — 옛 날짜를 손으로 다시 돌리다 실패하면 최신 카드를 낡은 것처럼 적었다
  if (asOf && run.trade_date && run.trade_date <= asOf) return null;
  let 까닭: string | null = run.error_text;
  try {
    const log = run.step_log ? (JSON.parse(run.step_log) as { reason?: unknown }) : null;
    if (typeof log?.reason === "string") 까닭 = log.reason;
  } catch {
    // 깨진 기록이면 error_text 만
  }
  const 무엇 = run.status === "skipped" ? "건너뛰었습니다" : "실패했습니다";
  const 날 = run.trade_date ? `${run.trade_date} 기준 ` : "";
  const 뒤 = asOf ? `아래는 ${asOf} 기준 신호입니다` : "앞서 계산된 신호도 없습니다";
  return `${날}신호 계산을 ${무엇}${까닭 ? ` (${까닭})` : ""} — ${뒤}`;
}

/**
 * 1부의 과거 성과 요약 — 배치가 신호를 낼 때 읽은 성과 행을 `rationale_data.performance` 에 싣는다 (docs/infra.md 25.803, 추천 감사 #3).
 * 텔레그램 1부와 같은 글(창·기준일, CAGR·MDD·샤프). 웹은 창을 고르거나 다시 읽지 않는다. 그 전 신호에는 없다 — null
 */
export function performanceLine(raw: string | null): string | null {
  if (!raw) return null;
  let p: unknown;
  try {
    p = (JSON.parse(raw) as { performance?: unknown }).performance;
  } catch {
    return null;
  }
  if (!p || typeof p !== "object") return null;
  const o = p as Record<string, unknown>;
  const 수 = (k: string) => (typeof o[k] === "number" && Number.isFinite(o[k]) ? (o[k] as number) : null);
  const 부분: string[] = [];
  const cagr = 수("cagr"), mdd = 수("mdd"), sharpe = 수("sharpe");
  // 텔레그램 `report_sections._pct` 와 같은 모양 — 부호 없이, 반올림한 뒤 "-0.0%" 가 나오지 않게 (25.805, 교차검증)
  const 퍼센트 = (x: number) => `${(Number((x * 100).toFixed(1)) + 0).toFixed(1)}%`;
  if (cagr !== null) 부분.push(`CAGR ${퍼센트(cagr)}`);
  if (mdd !== null) 부분.push(`MDD ${퍼센트(mdd)}`);
  if (sharpe !== null) 부분.push(`샤프 ${sharpe.toFixed(2)}`);
  if (부분.length === 0) return null;
  const 창 = [typeof o.window === "string" ? o.window : null, typeof o.as_of === "string" ? `기준 ${o.as_of}` : null]
    .filter(Boolean).join(", ");
  return `과거${창 ? `(${창})` : ""} ${부분.join(" · ")}`;
}
