/**
 * 스크리너 필터와 질의 생성.
 *
 * 값을 바꿔 가며 목록을 보는 화면이다. **추천 리포트(텔레그램)는 이 조건을 쓰지 않는다** — 배치는 점수·신호로
 * 고른다(docs/infra.md 25.772 정정: 예전에 "같은 조건에서 나온다" 고 적었는데 배치 어디에도 스크리너 조건을 읽는 곳이 없다).
 *
 * **지금 거를 수 있는 것과 없는 것이 나뉜다.**
 *   거를 수 있음: 시총, 거래대금, PER, PBR, ROE, 부채비율, 영업이익률, 성장률
 *   아직 없음: 팩터 점수, 센티먼트 (스코어링 엔진을 붙인 뒤)
 *   조건부: CAGR, MDD, 샤프, 변동성 (시세 백필이 표본 하한을 넘긴 뒤)
 *
 * 비율(PER·PBR·ROE·부채비율·영업이익률·성장률)은 **배치가 저장한 팩터 원시값**(factors.raw_json)이다 (docs/infra.md 25.490).
 * 웹은 역수·× 100 으로 단위만 바꾼다. 화면에 팩터 기준일과 회계연도를 함께 보여 준다.
 *
 * **국내·미국은 탭으로 나눠 따로 조회한다** (2026-09-17). 통화와 단위가 다르다.
 *   국내  금액을 억원으로 받는다
 *   미국  금액을 백만달러($M)로 받는다. 비율은 국내와 같이 배치 팩터 값이다(25.490 — 옛 "재무 수집 경로가 없다" 는 25.249 에서 틀렸다)
 */

import { z } from "zod";
import { localDate } from "@/lib/market";

const range = z
  .object({ min: z.number().nullable(), max: z.number().nullable() })
  .refine((r) => r.min === null || r.max === null || r.min <= r.max, {
    message: "최솟값이 최댓값보다 클 수 없습니다",
  });

const emptyRange = { min: null, max: null };

export const SORT_FIELDS = {
  market_cap: "시가총액",
  avg_turnover_20d: "거래대금",
  per: "PER",
  pbr: "PBR",
  roe: "ROE",
  operating_margin: "영업이익률",
  revenue_growth: "매출성장률",
  operating_income_growth: "영업이익성장률",
  cagr: "CAGR",
  mdd: "MDD",
  sharpe: "샤프",
  volatility_ann: "변동성",
  sentiment: "뉴스 감성",
} as const;

export type SortField = keyof typeof SORT_FIELDS;

/**
 * 나라별로 고를 수 있는 시장. `stocks.market` 에 실제로 있는 값이다.
 *
 * **셋만 적혀 있었다** (2026-09-23, docs/infra.md 25.184). 미국 시장 이름의 정의처는
 * `batch/sources/nasdaq_symbols.EXCHANGE_CODES` 이고 거기에는 여섯이 있다 —
 * `NYSE Arca`·`Cboe BZX`·`IEX` 종목은 **시장 필터로 고를 수가 없었다.**
 * docs/factors.md 가 실측을 적어 둔다: Cboe BZX 4종목, NYSE Arca 12종목.
 * `tests/test_market_names.py` 가 파이썬 쪽 목록과 이 목록을 대 본다.
 */
export const MARKETS_BY_COUNTRY = {
  KR: [
    ["ALL", "전체"],
    ["KOSPI", "코스피"],
    ["KOSDAQ", "코스닥"],
  ],
  US: [
    ["ALL", "전체"],
    ["NASDAQ", "나스닥"],
    ["NYSE", "뉴욕"],
    ["NYSE American", "NYSE American"],
    ["NYSE Arca", "NYSE Arca"],
    ["Cboe BZX", "Cboe BZX"],
    ["IEX", "IEX"],
  ],
} as const;

/** 스키마가 받는 시장 이름 — 화면 목록과 같은 곳에서 나온다 */
const 시장_이름들 = [
  ...new Set([...MARKETS_BY_COUNTRY.KR, ...MARKETS_BY_COUNTRY.US].map(([code]) => code)),
] as [string, ...string[]];

/** 금액 입력 단위. 국내는 억원, 미국은 백만달러 */
export const MONEY_UNIT = { KR: { label: "억", factor: 100_000_000 }, US: { label: "$M", factor: 1_000_000 } } as const;

export const screenerFilterSchema = z.object({
  country: z.enum(["KR", "US"]).default("KR"),
  // **시장 목록에서 뽑는다** (docs/infra.md 25.259). 25.184 가 `MARKETS_BY_COUNTRY.US` 에 NYSE Arca·Cboe BZX·IEX 를 더했는데
  // 여기 손으로 적은 목록은 그대로라, 화면이 고를 수 있게 한 세 시장이 **늘 400** 이었다(프리셋 저장도)
  market: z.enum(시장_이름들).default("ALL"),

  /** 유니버스에 편입된 종목만 볼지. 끄면 제외 종목도 본다 */
  universe_only: z.boolean().default(true),

  market_cap: range.default(emptyRange), // 억원 단위로 받는다
  avg_turnover_20d: range.default(emptyRange), // 억원
  per: range.default(emptyRange),
  pbr: range.default(emptyRange),
  roe: range.default(emptyRange), // %
  debt_ratio: range.default(emptyRange), // %
  operating_margin: range.default(emptyRange), // %
  revenue_growth: range.default(emptyRange), // %
  operating_income_growth: range.default(emptyRange), // %

  /** 성과 지표. 시세가 표본 하한을 넘어야 값이 있다 */
  metric_window: z.enum(["1Y", "3Y", "5Y"]).default("1Y"),
  cagr: range.default(emptyRange), // %
  // % — 낙폭의 크기(양수). 30 이하 = 30% 넘게 빠진 적 없음 (25.770). **음수는 받지 않는다** (25.773, 교차검증) — 결과 열이 음수라
  // 옛 습관대로 −30 을 넣으면 늘 0건(최대)·늘 참(최소)이 되는데 "조건이 좁다" 로 잘못 안내됐다. 옛 음수 프리셋도 여기서 걸린다
  mdd: range
    .refine((r) => (r.min === null || r.min >= 0) && (r.max === null || r.max >= 0), {
      message: "MDD 낙폭은 크기(0 이상)로 넣습니다 — 30 이면 30% 넘게 빠진 적 없음",
    })
    .default(emptyRange),
  sharpe: range.default(emptyRange),
  volatility_ann: range.default(emptyRange), // %

  /**
   * 업종. "ALL" 이면 전체. 값은 stocks.sector 에 실제로 있는 문자열이다(배치가 넣은 그대로).
   * 목록은 API 가 그 나라의 DISTINCT 로 만들어 준다 — 화면이 업종 표를 따로 들고 있지 않는다.
   */
  sector: z.string().max(60).default("ALL"),

  /** 뉴스 감성(−100~+100). 센티먼트는 5팩터와 별도 축이라 여기서도 따로 건다 (CLAUDE.md) */
  sentiment: range.default(emptyRange),

  /** 적자 기업을 뺄지. PER 가 뜻을 잃는다 */
  exclude_loss_making: z.boolean().default(false),

  sort_by: z.enum(Object.keys(SORT_FIELDS) as [SortField, ...SortField[]])
    .default("market_cap"),
  sort_desc: z.boolean().default(true),
  limit: z.number().int().min(1).max(500).default(100),
}).refine(
  (f) => (MARKETS_BY_COUNTRY[f.country] as ReadonlyArray<readonly [string, string]>).some(([v]) => v === f.market),
  { message: "그 나라에 없는 시장입니다", path: ["market"] },
);

export type ScreenerFilters = z.infer<typeof screenerFilterSchema>;

// `DEFAULT_FILTERS` 가 여기 있었다 (2026-09-21 지움, docs/infra.md 25.49).
// 부르는 곳이 없었고, 있었다면 **미국에서 늘 빈 화면**이 됐다 — 스키마 기본값은
// `universe_only: true` 인데 미국은 유니버스 편입이 0개다. 아래 `defaultFilters(country)`
// 가 바로 그것을 다루려고 있다. 이름이 비슷한 지름길을 옆에 두지 않는다.

/**
 * 나라별 기본 조건.
 *
 * 미국은 "유니버스만" 을 끄고 전 종목을 거래대금 순으로 보여 준다. 미국 배치는 D1 임시 운영 중 쉬어(docs/infra.md 25.14)
 * 유니버스 편입이 비어 있을 수 있고, 켜 두면 늘 빈 화면이다. (예전 주석이 가리키던 `US_MARKET_CAP_PENDING` 은 없어졌다 — 25.249)
 */
export function defaultFilters(country: "KR" | "US"): ScreenerFilters {
  if (country === "US") {
    return screenerFilterSchema.parse({ country, universe_only: false, sort_by: "avg_turnover_20d" });
  }
  return screenerFilterSchema.parse({ country });
}

export interface ScreenerRow {
  stock_id: number;
  ticker: string;
  name: string;
  market: string;
  sector: string | null;
  market_cap: number | null;
  avg_turnover_20d: number | null;
  close: number | null;
  price_date: string | null;
  fiscal_year: number | null;
  per: number | null;
  pbr: number | null;
  roe: number | null;
  debt_ratio: number | null;
  operating_margin: number | null;
  revenue_growth: number | null;
  operating_income_growth: number | null;
  cagr: number | null;
  mdd: number | null;
  sharpe: number | null;
  volatility_ann: number | null;
  data_points: number | null;
  sentiment: number | null;
  sentiment_date: string | null;
  /** 시총·거래대금의 기준(유니버스 스냅샷 날). 25.364 */
  snapshot_date?: string | null;
  /** 성과 지표(CAGR·MDD·샤프)의 계산 기준일. 25.364 */
  metrics_asof?: string | null;
  /** 비율(PER·PBR·ROE 등)을 읽은 팩터 기준일(factors.as_of_date). 25.490 */
  factors_asof?: string | null;
}

/** 화면 단위(억원·$M)를 DB 단위(원·달러)로. */
function toBase(country: "KR" | "US") {
  return (value: number) => Math.round(value * MONEY_UNIT[country].factor);
}

interface Condition {
  sql: string;
  args: Array<string | number>;
}

function rangeCondition(
  expression: string,
  bounds: { min: number | null; max: number | null },
  transform: (v: number) => number = (v) => v,
): Condition[] {
  const out: Condition[] = [];
  if (bounds.min !== null) {
    out.push({ sql: `${expression} >= ?`, args: [transform(bounds.min)] });
  }
  if (bounds.max !== null) {
    out.push({ sql: `${expression} <= ?`, args: [transform(bounds.max)] });
  }
  return out;
}

/**
 * 조회 SQL 과 인자를 만든다.
 *
 * 비율은 배치가 저장한 팩터 원시값(factors.raw_json)을 읽어 단위만 바꾼다 (docs/infra.md 25.490).
 * 사업연도·순이익은 최근 사업보고서 행에서, 성과 지표는 선택한 기간의 최신 행에서 가져온다.
 */
export function buildQuery(filters: ScreenerFilters): {
  sql: string;
  args: Array<string | number>;
} {
  // 안쪽 WHERE 의 인자. 조건을 미는 순서가 곧 ? 순서다
  const whereArgs: Array<string | number> = [filters.country];
  // 주식만 — 추천·보유 ETF 를 stocks 에 이었다(docs/infra.md 25.896). 종목 찾기는 보통주 잣대라 ETF 를 섞지 않는다
  const where: string[] = ["s.country = ?", "s.status = 'active'", "s.asset_type = 'stock'"];

  if (filters.market !== "ALL") {
    where.push("s.market = ?");
    whereArgs.push(filters.market);
  }
  if (filters.sector !== "ALL") {
    where.push("s.sector = ?");
    whereArgs.push(filters.sector);
  }

  // 바깥 WHERE(계산된 값)의 인자
  const args: Array<string | number> = [];

  const conditions: Condition[] = [
    ...rangeCondition("market_cap", filters.market_cap, toBase(filters.country)),
    ...rangeCondition("avg_turnover_20d", filters.avg_turnover_20d, toBase(filters.country)),
    ...rangeCondition("per", filters.per),
    ...rangeCondition("pbr", filters.pbr),
    ...rangeCondition("roe", filters.roe),
    ...rangeCondition("debt_ratio", filters.debt_ratio),
    ...rangeCondition("operating_margin", filters.operating_margin),
    ...rangeCondition("revenue_growth", filters.revenue_growth),
    ...rangeCondition("operating_income_growth", filters.operating_income_growth),
    ...rangeCondition("cagr * 100", filters.cagr),
    // **MDD 조건은 낙폭의 크기(양수 %)로 받는다** (docs/infra.md 25.770, 스크리너 감사). 저장값은 음수(−0.35 = 35% 하락)라
    // `mdd * 100 <= 30` 은 값이 있는 모든 종목이 통과했고, 조건 줄은 "MDD 30% 이하" 라 "낙폭 30% 이내" 로 읽혔다
    ...rangeCondition("-mdd * 100", filters.mdd),
    ...rangeCondition("sharpe", filters.sharpe),
    ...rangeCondition("volatility_ann * 100", filters.volatility_ann),
    ...rangeCondition("sentiment", filters.sentiment),
  ];

  // 계산된 값(PER 등)으로 거르려면 바깥에서 감싸야 한다.
  // GROUP BY 없는 HAVING 은 파서가 거부한다.
  const outerWhere: string[] = [];
  for (const condition of conditions) {
    outerWhere.push(condition.sql);
    args.push(...condition.args);
  }

  if (filters.exclude_loss_making) {
    outerWhere.push("net_income > 0");
  }

  if (filters.universe_only) {
    where.push("u.included = 1");
  }

  const orderExpression = ORDER_EXPRESSION[filters.sort_by];
  const direction = filters.sort_desc ? "DESC" : "ASC";

  // 비율(PER·PBR·ROE·부채비율·영업이익률·성장률)은 **배치가 저장한 값**(factors.raw_json)을 읽는다 (docs/infra.md 25.490).
  // 예전에는 여기서 financials 를 나눴다 — CLAUDE.md "웹앱은 계산하지 않는다" 의 예외였고, 식도 배치와 달라(주간 스냅샷 시총 ÷
  // 최근 사업연도) 같은 종목의 스크리너 PER 과 점수 E/P 역수가 어긋났다(25.488). 지금 하는 일은 역수·× 100 **단위 바꾸기**뿐이다.
  // 사업연도(fiscal_year)와 순이익(적자 제외 조건)은 저장된 사업보고서 값을 그대로 읽는다(나눗셈 없음).
  // 분기를 섞지 않도록 사업보고서(11011)만 쓴다.
  // 유니버스 스냅샷은 나라마다 날짜가 다르다(국내 09-16, 미국 09-15 처럼).
  // 전체 MAX 로 잡으면 한 나라가 통째로 빠진다. 나라의 최신 스냅샷을 따로 잡는다.
  const inner = `
WITH snapshot AS (
  SELECT MAX(um.snapshot_date) AS d
  FROM universe_members um JOIN stocks s2 ON s2.id = um.stock_id
  WHERE s2.country = ?
),
-- 지금 유니버스 종목 수 — 성과 지표 배치는 최신 스냅샷의 편입 종목만 계산한다(jobs/metrics.load_targets) (25.782)
universe_n AS (
  SELECT COUNT(*) AS c FROM universe_members um2 JOIN stocks s5 ON s5.id = um2.stock_id
  WHERE s5.country = ? AND um2.included = 1 AND um2.snapshot_date = (SELECT d FROM snapshot)
),
metrics_asof AS (
  -- **성과 지표 기준일도 나라별로 잡는다** (2026-09-22, docs/infra.md 25.112).
  -- 바로 위 스냅샷과 같은 이유인데 여기만 전체 MAX 였다. 나라마다 jobs/metrics 가 따로
  -- 도는데(지금은 미국이 쉰다 — 25.14) 전체에서 잡으면 늦은 쪽 종목의 CAGR·MDD·샤프가
  -- 통째로 NULL 이 된다. LEFT JOIN 이라 행은 남아서 화면은 "지표가 아직 없나 보다" 로 보인다
  -- **그 기간(window)의, 넓게 계산된 날**을 잡는다 (docs/infra.md 25.775·25.779·25.782). 나라 전체 MAX 였을 때는 수동 실행
  -- (metrics.yml 의 ticker·window)으로 한 종목만 새 날짜가 되면 나머지 종목의 CAGR·MDD·샤프가 모두 NULL 이 됐다.
  -- 넓다 = **지금 유니버스의 절반 이상**을 계산한 날(종목 수로 센다 — 같은 날 새 판으로 다시 돌리면 행이 쌓인다, 25.779).
  -- 25.779 는 "마지막 날부터 40일 안의 최대" 와 견줘, 배치가 40일 넘게 멈춘 뒤 한 종목만 돌린 날이 다시 기준일이 됐다(교차검증).
  -- 그런 날이 없으면(유니버스가 두 배 넘게 늘었다) 가장 넓게 계산된 날. 표를 한 번만 훑는다
  SELECT d FROM (
    SELECT m.as_of_date AS d, COUNT(DISTINCT m.stock_id) AS n
    FROM performance_metrics m JOIN stocks s3 ON s3.id = m.stock_id
    WHERE s3.country = ? AND m.window = ?
    GROUP BY m.as_of_date
  )
  ORDER BY (n * 2 >= (SELECT c FROM universe_n)) DESC,
           CASE WHEN n * 2 >= (SELECT c FROM universe_n) THEN d END DESC, n DESC, d DESC
  LIMIT 1
),
-- 팩터 기준일과 판: **나라 기준, 바깥 행을 가리키지 않는 비상관 하위질의**다 (docs/infra.md 25.428 — 상관 하위질의로 잡았더니
-- 읽는 행이 수백 배가 됐다). 한 번만 계산되고 아래 LEFT JOIN 이 그 두 값으로 색인(idx_factors_date·UNIQUE)을 탄다
factors_asof AS (
  -- **DESC LIMIT 1 로 첫 값에서 멈출 수 있게 쓴다** (docs/infra.md 25.491·25.497, 교차검증). 통계(ANALYZE)가 있는 SQLite 에서
  -- MAX() 는 모든 기준일 × 5팩터를 읽는 계획을 골랐다(1년치 약 110만 행). 통계가 없으면 둘 다 싸다. 다른 나라 행이 더 최근이면
  -- 그 행들을 지나쳐 읽는다(미국이 쉬는 동안 미국 탭). D1·libsql 이 어느 계획을 고르는지는 [확인필요]
  SELECT fx.as_of_date AS d
  FROM factors fx JOIN stocks s4 ON s4.id = fx.stock_id
  WHERE s4.country = ?
  ORDER BY fx.as_of_date DESC LIMIT 1 -- 날짜만 고른다. 판은 아래 factors_ver 가 calc_version 으로 고른다(여기 넣으면 색인을 못 탄다)
),
factors_ver AS (
  -- 그 날짜의 가장 새 판. 판을 올려 다시 돌리면 같은 (종목, 기준일, 팩터)에 두 행이 남는다 — 한 종목이 두 번 나오지 않게
  SELECT MAX(fc.calc_version) AS v
  FROM factors fc JOIN stocks s5 ON s5.id = fc.stock_id
  WHERE s5.country = ? AND fc.as_of_date = (SELECT d FROM factors_asof)
),
-- 재무 기준: 연결이 있으면 연결, 없으면 별도 (CLAUDE.md "연결 기준 우선", docs/infra.md 25.314).
-- 사업연도·순이익 표시용이다. 비율은 배치 값이라 이 선택을 다시 하지 않는다(배치 db.FINANCIAL_BASIS_* 가 한다)
-- 가장 늦은 사업연도의 기준을 따른다 — 연결을 내다 별도만 내게 된 회사가 옛 연결 연도에 묶였다 (25.550)
basis AS (
  SELECT f.stock_id,
    (SELECT fb.consolidated FROM financials fb WHERE fb.stock_id = f.stock_id AND fb.report_code = '11011'
     ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1) AS c
  FROM financials f
  WHERE f.report_code = '11011'
    -- 그 스냅샷의 종목만 (25.859) — 예전에는 재무 표 전체(나라 구분도 없이)를 묶었다
    AND f.stock_id IN (SELECT um3.stock_id FROM universe_members um3 WHERE um3.snapshot_date = (SELECT d FROM snapshot))
  GROUP BY f.stock_id
),
latest_annual AS (
  SELECT f0.stock_id, MAX(f0.fiscal_year) AS fy
  FROM financials f0 JOIN basis b0 ON b0.stock_id = f0.stock_id AND f0.consolidated = b0.c
  WHERE f0.report_code = '11011'
  GROUP BY f0.stock_id
)
SELECT
  s.id AS stock_id, s.ticker, COALESCE(s.name_ko, s.name_en) AS name,
  s.market, s.sector,
  u.market_cap, u.avg_turnover_20d,
  p.close, p.date AS price_date,
  f.fiscal_year,
  -- 배치 값의 단위만 바꾼다 (docs/screener.md 6장 표). ep·bp ≤ 0(적자·자본잠식)이면 배수가 뜻이 없어 비운다
  CASE WHEN json_extract(fv.raw_json, '$.ep') > 0 THEN 1.0 / json_extract(fv.raw_json, '$.ep') END AS per,
  CASE WHEN json_extract(fv.raw_json, '$.bp') > 0 THEN 1.0 / json_extract(fv.raw_json, '$.bp') END AS pbr,
  json_extract(fq.raw_json, '$.roe') * 100 AS roe,
  -- 부채총계가 빈 종목의 대체식(자산 − 자본, 25.239)은 배치 scoring.quality_inputs 가 이미 적용한 값이다
  json_extract(fq.raw_json, '$.debt_ratio') * 100 AS debt_ratio,
  json_extract(fq.raw_json, '$.operating_margin') * 100 AS operating_margin,
  json_extract(fg.raw_json, '$.revenue_growth') * 100 AS revenue_growth,
  json_extract(fg.raw_json, '$.operating_income_growth') * 100 AS operating_income_growth,
  pm.cagr, pm.mdd, pm.sharpe, pm.volatility_ann, pm.data_points,
  ss.sentiment, ss.as_of_date AS sentiment_date,
  -- 값마다 **언제 기준인지** (docs/infra.md 25.364). 시총·거래대금은 스냅샷 날, 성과 지표는 계산 기준일, 비율은 팩터 기준일
  (SELECT d FROM snapshot) AS snapshot_date, (SELECT d FROM metrics_asof) AS metrics_asof,
  (SELECT d FROM factors_asof) AS factors_asof,
  f.net_income
FROM stocks s
JOIN universe_members u
  ON u.stock_id = s.id
 AND u.snapshot_date = (SELECT d FROM snapshot)
LEFT JOIN latest_annual la ON la.stock_id = s.id
LEFT JOIN basis fb ON fb.stock_id = s.id
LEFT JOIN financials f
  ON f.stock_id = s.id AND f.fiscal_year = la.fy
 AND f.report_code = '11011' AND f.consolidated = fb.c
LEFT JOIN factors fv
  ON fv.stock_id = s.id AND fv.factor = 'value'
 AND fv.as_of_date = (SELECT d FROM factors_asof) AND fv.calc_version = (SELECT v FROM factors_ver)
LEFT JOIN factors fq
  ON fq.stock_id = s.id AND fq.factor = 'quality'
 AND fq.as_of_date = (SELECT d FROM factors_asof) AND fq.calc_version = (SELECT v FROM factors_ver)
LEFT JOIN factors fg
  ON fg.stock_id = s.id AND fg.factor = 'growth'
 AND fg.as_of_date = (SELECT d FROM factors_asof) AND fg.calc_version = (SELECT v FROM factors_ver)
-- 종가: 스냅샷 전후 한 달 안의 그 종목 마지막 날. **종목마다 주키(stock_id, date)로 찾는다** (docs/infra.md 25.859, 감사).
-- 예전 CTE(latest_price)는 "30일 창이라 색인 범위 조회" 라고 적었지만 실제 계획은 가격 표 전체를 훑어(SCAN prices) 한 번에 약 110만 행이었다.
-- 한 달 넘게 가격이 없는 종목은 종가를 비워 둔다(예전과 같다)
LEFT JOIN prices p ON p.stock_id = s.id AND p.date = (
  SELECT MAX(p2.date) FROM prices p2
  WHERE p2.stock_id = s.id AND p2.date >= date((SELECT d FROM snapshot), '-30 days'))
-- 계산 판까지 봐야 한 행이다 (2026-09-22, docs/infra.md 25.102).
-- 판을 올리면 같은 (종목, 기준일, 창)에 두 행이 남아 스크리너가 한 종목을 두 번 보여 준다
LEFT JOIN performance_metrics pm
  ON pm.stock_id = s.id AND pm.window = ?
 AND pm.as_of_date = (SELECT d FROM metrics_asof)
 AND pm.calc_version = (SELECT MAX(c.calc_version) FROM performance_metrics c
                        WHERE c.stock_id = pm.stock_id AND c.as_of_date = pm.as_of_date
                          AND c.window = pm.window)
-- 감성은 수집한 상위 종목에만 있다(설정 sentiment_target_top_n). 없으면 NULL 이고 그 조건을 걸면 빠진다
LEFT JOIN sentiment_scores ss
  ON ss.stock_id = s.id
 AND ss.as_of_date = (SELECT MAX(x.as_of_date) FROM sentiment_scores x WHERE x.stock_id = s.id)
 -- **묵은 감성은 쓰지 않는다** (docs/infra.md 25.577, 감사). 배치는 점수 기준일에서 3일 넘은 감성을 버린다
 -- (services.sentiment.MAX_AGE_DAYS) — 여기는 나이 제한이 없어, 수집 대상에서 빠진 종목의 몇 달 전 80점이 감성 조건·정렬
 -- 상위에 올랐고 같은 종목 상세("3일 넘게 갱신되지 않음")와 말이 달랐다. 기준은 팩터 기준일(= 점수 기준일)
 -- 팩터가 아직 없는 나라는 오늘을 기준으로 둔다 — NULL 이면 비교가 NULL 이 되어 그 나라 감성이 모두 말없이 사라졌다 (25.579, 교차검증)
 AND ss.as_of_date >= date(COALESCE((SELECT d FROM factors_asof), date('now')), '-3 days')
WHERE ${where.join(" AND ")}`;

  const sql = `SELECT * FROM (${inner}) t
${outerWhere.length ? `WHERE ${outerWhere.join(" AND ")}` : ""}
ORDER BY ${orderExpression} ${direction} NULLS LAST, stock_id
LIMIT ?`;

  // 인자 순서가 SQL 의 ? 순서와 같아야 한다.
  //   snapshot 의 나라 → universe_n 의 나라(25.782) → metrics_asof 의 나라·기간(25.775) → factors_asof 의 나라
  //   → factors_ver 의 나라 → JOIN 의 metric_window → WHERE 의 나라·시장·업종 → 바깥 조건 → LIMIT
  return {
    sql,
    args: [
      filters.country,
      filters.country,
      filters.country,
      filters.metric_window,
      filters.country,
      filters.country,
      filters.metric_window,
      ...whereArgs,
      ...args,
      filters.limit,
    ],
  };
}

const ORDER_EXPRESSION: Record<SortField, string> = {
  market_cap: "market_cap",
  avg_turnover_20d: "avg_turnover_20d",
  per: "per",
  pbr: "pbr",
  roe: "roe",
  operating_margin: "operating_margin",
  revenue_growth: "revenue_growth",
  operating_income_growth: "operating_income_growth",
  cagr: "cagr",
  mdd: "mdd",
  sharpe: "sharpe",
  volatility_ann: "volatility_ann",
  sentiment: "sentiment",
};

/** 조건을 사람이 읽는 문장으로. 화면 상단(`/api/screener` 의 conditions)만 쓴다 — CSV·텔레그램은 쓰지 않는다 (25.772·25.774). */
/**
 * **값으로 거르는 조건들** (docs/infra.md 25.149).
 *
 * 하나라도 걸면 그 값이 `NULL` 인 종목은 **전부 빠진다** — SQL 의 `NULL >= 3` 은 참이
 * 아니라 `NULL` 이고, `WHERE` 는 참이 아닌 행을 버린다. 그래서 "조건에 안 맞는 것" 과
 * "값이 아직 없는 것" 이 결과에서 **똑같이 사라진다.**
 *
 * `key` 는 필터 이름이고 `field` 는 결과 행의 칸 이름이다. 둘이 다른 것이 있어
 * (`cagr` 은 SQL 에서 `cagr * 100`) 표로 둔다.
 */
export const VALUE_FILTERS: Array<{
  key: keyof ScreenerFilters;
  field: keyof ScreenerRow;
  label: string;
}> = [
  { key: "market_cap", field: "market_cap", label: "시가총액" },
  { key: "avg_turnover_20d", field: "avg_turnover_20d", label: "20일 평균 거래대금" },
  { key: "per", field: "per", label: "PER" },
  { key: "pbr", field: "pbr", label: "PBR" },
  { key: "roe", field: "roe", label: "ROE" },
  { key: "debt_ratio", field: "debt_ratio", label: "부채비율" },
  { key: "operating_margin", field: "operating_margin", label: "영업이익률" },
  { key: "revenue_growth", field: "revenue_growth", label: "매출 성장률" },
  { key: "operating_income_growth", field: "operating_income_growth", label: "영업이익 성장률" },
  { key: "cagr", field: "cagr", label: "CAGR" },
  { key: "mdd", field: "mdd", label: "MDD" },
  { key: "sharpe", field: "sharpe", label: "샤프지수" },
  { key: "volatility_ann", field: "volatility_ann", label: "연환산 변동성" },
  { key: "sentiment", field: "sentiment", label: "감성 점수" },
];

/** 지금 걸려 있는 값 조건들 */
export function usedValueFilters(filters: ScreenerFilters): typeof VALUE_FILTERS {
  return VALUE_FILTERS.filter((f) => {
    const b = filters[f.key] as { min: number | null; max: number | null } | undefined;
    return !!b && (b.min !== null || b.max !== null);
  });
}

/** 값 조건을 전부 뺀 같은 조회. 0건일 때 "조건이 좁아서인가, 값이 없어서인가" 를 가린다 */
export function withoutValueFilters(filters: ScreenerFilters): ScreenerFilters {
  const out = { ...filters };
  for (const f of VALUE_FILTERS) {
    (out[f.key] as { min: number | null; max: number | null }) = { min: null, max: null };
  }
  out.exclude_loss_making = false;
  return out;
}

/**
 * 0건일 때 **왜 비었나** (docs/infra.md 25.149).
 *
 * 세 가지를 가른다 — 25.86 이 추천 화면에서 가른 것과 같은 셋이다.
 *
 *   1. 조건을 다 빼도 종목이 없다 → 유니버스·종목 마스터 문제다
 *   2. 종목은 있는데 **건 조건의 값이 하나도 없다** → 자료가 아직 없다. 범위를 넓혀도 안 나온다
 *   3. 종목도 값도 있다 → 정말로 조건이 좁다. 그때만 "범위를 넓혀 보세요" 가 맞는 말이다
 *
 * `후보` 는 값 조건을 뺀 결과 행이다. 정렬·상한이 같으므로 **사용자가 보게 될 그 집합**이다.
 * 상한에 걸렸으면 전체가 아니라 앞부분이다 — 그때는 "없다" 고 단정하지 않는다 (25.363).
 */
/** 편입이 0 이면 배치 고장을 의심할 만한 큰 시장 (25.708) */
const 주요_시장 = new Set(["KOSPI", "KOSDAQ", "NYSE", "NASDAQ"]);

export function whyEmpty(filters: ScreenerFilters, 후보: ScreenerRow[]): string[] {
  const 걸린것 = usedValueFilters(filters);
  if (후보.length === 0) {
    // **값 조건만 뺐다** (docs/infra.md 25.682, 감사). 시장·업종은 진단 조회에도 그대로 걸려 있어, 종목이 없는 업종을
    // 고른 0건을 "종목 마스터나 유니버스가 비어 있을 수 있습니다" 로 단정했다 — 멀쩡한 배치를 의심하게 했다
    // **업종만** 이 분기로 간다 (25.686, 교차검증). 업종 고르개는 stocks 전체의 업종이라 유니버스 편입 0 이 정상일 수
    // 있지만, 코스피 같은 **시장**의 편입이 0 인 것은 사실상 배치 고장이다 — 그때는 아래 예전 문장이 맞다
    // 미국의 작은 거래소(NYSE American·Arca·Cboe BZX·IEX…)도 편입 0 이 정상일 수 있다 — 고장으로 안내하지 않는다 (25.708, 교차검증)
    if (filters.market !== "ALL" && !주요_시장.has(filters.market)) {
      return [
        `값 조건을 다 빼도 시장 ${filters.market}${filters.universe_only ? " 의 유니버스 편입" : "에"} 종목이 없습니다.` +
          " 값 조건 탓이 아닙니다 — 작은 거래소는 편입 종목이 없을 수 있습니다. 시장" +
          (filters.universe_only ? "이나 [유니버스만]" : "") +
          " 을 바꿔 보세요",
      ];
    }
    if (filters.sector !== "ALL") {
      return [
        `값 조건을 다 빼도 업종 ${filters.sector}${filters.universe_only ? " 의 유니버스 편입" : "에"} 종목이 없습니다.` +
          " 값 조건 탓이 아닙니다 — 업종" +
          (filters.universe_only ? "이나 [유니버스만]" : "") +
          " 을 바꿔 보세요",
      ];
    }
    return [
      "조건을 다 빼도 종목이 없습니다. 조건이 좁아서가 아닙니다 —" +
        " 종목 마스터나 유니버스가 비어 있을 수 있습니다. [시스템 상태]의 데이터 신선도를 보세요",
    ];
  }
  const 빈칸 = 걸린것.filter((f) => 후보.every((r) => r[f.field] === null));
  if (빈칸.length > 0) {
    // **상한에 걸렸으면 단정하지 않는다** (docs/infra.md 25.363). `후보` 는 정렬·상한(`limit`)이 걸린 부분집합이다.
    // 시총 오름차순 100개(소형주)가 모두 감성 없음이어도 뒤쪽 종목에는 값이 있다 — "넓혀도 안 나온다" 는 거짓이었다
    if (후보.length >= filters.limit) {
      return [
        `${빈칸.map((f) => f.label).join(" · ")} 조건을 걸었는데,` +
          ` 조건을 뺀 결과의 앞 ${후보.length}종목(지금 정렬 기준)은 모두 그 값이 비어 있습니다.` +
          " 정렬 뒤쪽 종목에는 값이 있을 수 있습니다 — 정렬을 바꾸거나 개수를 늘려 보세요",
      ];
    }
    return [
      `${빈칸.map((f) => f.label).join(" · ")} 조건을 걸었는데,` +
        ` 조건을 뺀 ${후보.length}종목 모두 그 값이 비어 있습니다.` +
        " **범위를 넓혀도 나오지 않습니다** — 그 자료가 채워져야 합니다",
    ];
  }
  if (걸린것.length === 0 && !filters.exclude_loss_making) {
    return [];
  }
  return [
    `조건을 다 빼면 ${후보.length}종목입니다. 값은 있는데 조건이 좁아 0건입니다 —` +
      " 범위를 넓혀 보세요",
  ];
}

export function describe(filters: ScreenerFilters): string[] {
  const out: string[] = [];
  const nation = filters.country === "KR" ? "국내" : "미국";
  const market = filters.market === "ALL" ? `${nation} 전체` : filters.market;
  out.push(`${market}${filters.universe_only ? " 유니버스 편입" : " 전 종목"}`);

  const unit = MONEY_UNIT[filters.country].label;
  const labelled: Array<[string, { min: number | null; max: number | null }, string]> = [
    ["시가총액", filters.market_cap, unit],
    ["거래대금", filters.avg_turnover_20d, unit],
    ["PER", filters.per, "배"],
    ["PBR", filters.pbr, "배"],
    ["ROE", filters.roe, "%"],
    ["부채비율", filters.debt_ratio, "%"],
    ["영업이익률", filters.operating_margin, "%"],
    ["매출성장률", filters.revenue_growth, "%"],
    ["영업이익성장률", filters.operating_income_growth, "%"],
    [`CAGR(${filters.metric_window})`, filters.cagr, "%"],
    [`MDD 낙폭(${filters.metric_window})`, filters.mdd, "%"],
    [`샤프(${filters.metric_window})`, filters.sharpe, ""],
    [`변동성(${filters.metric_window})`, filters.volatility_ann, "%"],
    ["뉴스 감성", filters.sentiment, "점"],
  ];
  if (filters.sector !== "ALL") out.push(`업종 ${filters.sector}`);

  for (const [label, bounds, unit] of labelled) {
    if (bounds.min === null && bounds.max === null) continue;
    if (bounds.min !== null && bounds.max !== null) {
      out.push(`${label} ${bounds.min}~${bounds.max}${unit}`);
    } else if (bounds.min !== null) {
      out.push(`${label} ${bounds.min}${unit} 이상`);
    } else {
      out.push(`${label} ${bounds.max}${unit} 이하`);
    }
  }

  if (filters.exclude_loss_making) out.push("적자 제외");
  return out;
}


// ----------------------------------------------------------------------
// 저장한 조건 (docs/screener.md 2장)
// ----------------------------------------------------------------------

export const presetInputSchema = z.object({
  name: z.string().trim().min(1, "이름을 넣으세요").max(30, "이름은 30자까지"),
  filters: screenerFilterSchema,
});
export type PresetInput = z.infer<typeof presetInputSchema>;

export interface PresetRow {
  id: number;
  name: string;
  country: string;
  filters_json: string;
  created_at: string;
  last_used_at: string | null;
}

export const PRESET_LIST = `SELECT id, name, country, filters_json, created_at, last_used_at
FROM screener_presets WHERE country = ? ORDER BY name`;
export const PRESET_UPSERT = `INSERT INTO screener_presets (name, country, filters_json, created_at) VALUES (?, ?, ?, ?)
ON CONFLICT (country, name) DO UPDATE SET filters_json = excluded.filters_json`;
export const PRESET_DELETE = `DELETE FROM screener_presets WHERE id = ?`;
export const PRESET_TOUCH = `UPDATE screener_presets SET last_used_at = ? WHERE id = ?`;

/**
 * 25.770 전의 MDD 조건(음수 — `mdd * 100` 에 걸던 값)을 **낙폭 크기**로 옮긴다 (docs/infra.md 25.774, 교차검증).
 * 옛 `{min:-30}` 은 "mdd×100 ≥ −30" = "낙폭 30% 이내" 라 새 `{max:30}` 이다 — 경계가 뒤집힌다(새 min = −옛 max, 새 max = −옛 min).
 * 음수가 하나라도 있거나 `{max:0}`(최소 없음)이면 옛 모양으로 본다(25.773·25.776). 새 모양 `{max:0}`("낙폭 0 인 종목만")도
 * 옛 것으로 읽혀 `{min:0}` 이 되는데, 판 표시가 없어 가를 수 없어 받아들인 한계다(25.779). 옮기지 않으면 25.773 의 음수 거부에
 * 걸려 MDD 와 상관없는 다른 조건까지 통째로 못 쓰게 됐다.
 */
export function migrateOldMdd(value: unknown): unknown {
  if (!value || typeof value !== "object") return value;
  const { min, max } = value as { min?: unknown; max?: unknown };
  // 옛 {max:0}("mdd×100 ≤ 0" = 값 있는 모든 종목)도 옛 모양이다 — 그대로 두면 새 뜻 "낙폭 0 인 종목만" 으로 뒤집힌다 (25.776)
  const 옛 =
    (typeof min === "number" && min < 0) || (typeof max === "number" && max < 0) || ((min ?? null) === null && max === 0);
  if (!옛) return value;
  // 뒤집은 값이 음수면(부호가 섞인 옛 값 — 그쪽 경계는 늘 참이었다) 조건 없음으로 둔다 (25.776, 교차검증)
  const 뒤집기 = (x: unknown) => (typeof x === "number" ? (x === 0 ? 0 : -x > 0 ? -x : null) : null);
  return { min: 뒤집기(max), max: 뒤집기(min) };
}

/** 저장된 조건을 푼다. 항목이 늘거나 깨졌으면 기본값으로 메운다 — 옛 저장분이 화면을 막지 않게 */
export function parsePreset(row: PresetRow): ScreenerFilters | null {
  try {
    const raw = JSON.parse(row.filters_json);
    if (raw && typeof raw === "object") raw.mdd = migrateOldMdd(raw.mdd);
    const parsed = screenerFilterSchema.safeParse({ ...defaultFilters(row.country === "US" ? "US" : "KR"), ...raw });
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

// ----------------------------------------------------------------------
// CSV 내보내기 (docs/screener.md 3장). 화면 표와 같은 값을 파일로. 계산하지 않는다
// ----------------------------------------------------------------------

/**
 * 성과 지표가 한 행도 없을 때 **왜 비었는지** (route 가 안내에 싣는다). 표본이 모자라 계산하지 않은 것이지 값이 0 인 것이 아니다.
 * 기준일조차 없으면 **그 기간은 이 나라에서 한 번도 계산된 적이 없다** (docs/infra.md 25.779, 교차검증) — 25.775 가 기간별 기준일을
 * 잡으면서 가를 수 있게 됐는데 여전히 "표본이 모자랍니다" 라고 했다
 */
export function metricsEmptyNote(
  rows: Array<Pick<ScreenerRow, "cagr" | "data_points" | "metrics_asof">>,
  window: string,
): string | null {
  if (rows.length === 0 || rows.some((r) => r.cagr !== null)) return null;
  if (!rows[0].metrics_asof) {
    return `${window} 성과 지표는 이 나라에서 아직 한 번도 계산되지 않았습니다. 성과 지표 배치가 그 기간을 돌면 채워집니다`;
  }
  const points = rows.find((r) => r.data_points !== null)?.data_points;
  return (
    `성과 지표는 아직 계산되지 않았습니다. 표본이 모자랍니다` +
    (points ? ` (현재 ${points} 거래일)` : "") +
    `. 시세 백필이 끝나면 채워집니다`
  );
}

export const CSV_COLUMNS: Array<[keyof ScreenerRow, string]> = [
  ["ticker", "종목코드"], ["name", "종목명"], ["market", "시장"], ["sector", "업종"],
  ["close", "종가"], ["price_date", "종가일"], ["market_cap", "시가총액"], ["avg_turnover_20d", "20일 평균 거래대금"],
  ["fiscal_year", "회계연도"], ["factors_asof", "비율 기준일"], ["per", "PER"], ["pbr", "PBR"], ["roe", "ROE(%)"], ["debt_ratio", "부채비율(%)"],
  ["operating_margin", "영업이익률(%)"], ["revenue_growth", "매출성장률(%)"], ["operating_income_growth", "영업이익성장률(%)"],
  ["cagr", "CAGR(%)"], ["mdd", "MDD(%)"], ["sharpe", "샤프"], ["volatility_ann", "변동성(%)"],
  // 성과 지표 기준일 (25.779, 교차검증) — 25.775 부터 일부러 최신보다 늦을 수 있어 파일만 보고도 언제 값인지 알아야 한다
  ["metrics_asof", "성과 지표 기준일"],
  ["sentiment", "뉴스 감성"], ["sentiment_date", "감성 기준일"],
];

/** 그 나라에 실제로 있는 업종 목록. 배치가 넣은 값을 그대로 고르게 한다 */
export const SECTORS = `SELECT DISTINCT sector FROM stocks
WHERE country = ? AND status = 'active' AND sector IS NOT NULL AND sector <> '' ORDER BY sector`;

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** BOM 을 붙인다. 엑셀이 UTF-8 한글을 깨뜨리지 않게 */
/**
 * 행 값이 **비율(0.123)** 인데 열 이름이 "(%)" 인 칸 (docs/infra.md 25.577, 감사). 화면(`ScreenerForm`)과 필터는 ×100 을
 * 하는데 CSV 만 비율 그대로 내 CAGR 12.3% 가 "CAGR(%) 0.123" 으로 나갔다. ROE 등은 SQL 이 이미 ×100 이다
 */
const CSV_RATIO_AS_PERCENT: ReadonlySet<keyof ScreenerRow> = new Set(["cagr", "mdd", "volatility_ann"] as const);

export function toCsv(rows: ScreenerRow[], metricWindow: string | null = null): string {
  // 성과 지표 열 이름에 기간을 붙인다 — "CAGR(3Y)(%)" (25.777). 화면(25.772)과 같다
  const 성과 = new Set<keyof ScreenerRow>(["cagr", "mdd", "sharpe", "volatility_ann"]);
  const head = CSV_COLUMNS.map(([key, label]) =>
    metricWindow && 성과.has(key) ? label.replace(/^([^(]+)/, `$1(${metricWindow})`) : label,
  ).join(",");
  const body = rows.map((r) =>
    CSV_COLUMNS.map(([key]) => {
      const v = r[key];
      return csvCell(CSV_RATIO_AS_PERCENT.has(key) && typeof v === "number" ? Math.round(v * 10_000) / 100 : v);
    }).join(","),
  );
  return "\uFEFF" + [head, ...body].join("\n") + "\n";
}

/**
 * 행마다 붙는 재무 사업연도 (docs/infra.md 25.486, 웹 감사). 예전에는 국내 행에만 붙어 미국 PER·ROE 가 몇 년도 재무인지
 * 행에서 볼 수 없었다. 미국은 회계연도가 달력과 다를 수 있어 "N년 결산" 으로 적는다
 */
export function fiscalYearLabel(country: "KR" | "US", fiscalYear: number | null | undefined): string {
  if (!fiscalYear) return "";
  // 미국 사업연도는 **기말일의 연도**다(sec_facts.fiscal_year_of). 2월 초 결산(Home Depot 등)은 회사가 부르는 "fiscal 2024" 와
  // 한 해 다르다 — "FY" 를 붙이면 회사 명칭처럼 읽혀 "결산" 으로 적는다 (25.487, 교차검증)
  return country === "KR" ? `${fiscalYear}년` : `${fiscalYear}년 결산`;
}

/** 파일 이름의 날짜는 **사용자 날짜(한국)** 다 (docs/infra.md 25.483) — UTC 로 자르면 00~09시 KST 에 전날이 붙었다 */
export function csvFileName(country: "KR" | "US", today: Date = new Date()): string {
  return `screener-${country}-${localDate("KR", today)}.csv`;
}

/** 점수에서 오는 비율 — 점수를 내지 않는 종목(유니버스 밖 등)은 비어 있다 (docs/screener.md 6) */
const 점수_비율 = ["per", "pbr", "roe", "debt_ratio", "operating_margin"] as const;
/** 조건에서 빠지는 쪽은 성장률도 같다 — 같은 팩터 표(growth 행)에서 온다 (docs/infra.md 25.500, 교차검증) */
const 점수_조건 = [...점수_비율, "revenue_growth", "operating_income_growth"] as const;

/**
 * **"유니버스만" 을 끄고 비율 조건을 걸면** 비율이 빈 종목이 조건에서 조용히 빠진다 (docs/infra.md 25.497, 교차검증).
 * 남은 행은 비율이 다 있어 기준 줄의 "비율 없는 N종목" 도 0 이라, 아무 말이 없었다. 빠진 수는 따로 세지 않는다
 * (조회를 한 번 더 해야 한다) — 빠진다는 사실만 말한다.
 */
export function ratioFilterNote(filters: ScreenerFilters): string | null {
  const 걸린_전부 = 점수_조건.filter((k) => filters[k].min !== null || filters[k].max !== null);
  // **성장률은 유니버스 안에서도 빈다** (docs/infra.md 25.511, 교차검증) — 전년 값이 0 이하면(흑자 전환·전년 적자)
  // 배치가 성장률을 내지 않는다(scoring.growth_rate). 유니버스만 볼 때도 성장률 조건이면 말한다
  if (filters.universe_only) {
    // 유니버스 안에서도 비는 까닭이 비율마다 다르다 (docs/infra.md 25.523, 교차검증) — PER 은 적자면(ep ≤ 0),
    // ROE·부채비율은 자본잠식이면, 성장률은 전년 값이 0 이하이거나 없으면(신규 상장) 배치가 값을 내지 않는다
    const 까닭: Partial<Record<(typeof 점수_조건)[number], string>> = {
      per: "PER(적자)", pbr: "PBR(자본잠식)", roe: "ROE(자본잠식)", debt_ratio: "부채비율(자본잠식)",
      operating_margin: "영업이익률(매출 없음)",
      revenue_growth: "매출성장률(전년 값이 0 이하이거나 없음)", operating_income_growth: "영업이익성장률(전년 값이 0 이하이거나 없음)",
    };
    const 걸린_까닭 = 걸린_전부.map((k) => 까닭[k]).filter((v): v is string => Boolean(v));
    if (걸린_까닭.length === 0) return null;
    return `조건을 건 ${걸린_까닭.join("·")} 값이 없는 종목은 결과에서 빠집니다`;
  }
  const 걸린 = 걸린_전부;
  if (걸린.length === 0) return null;
  const 이름 = { per: "PER", pbr: "PBR", roe: "ROE", debt_ratio: "부채비율", operating_margin: "영업이익률",
    revenue_growth: "매출성장률", operating_income_growth: "영업이익성장률" };
  // 실제로 빠진 종목이 없을 수도 있어 "빠집니다" 로 규칙을 말한다 — 빠진 수는 세지 않는다
  return `${걸린.map((k) => 이름[k]).join("·")} 조건을 걸면, 값이 없는 종목(점수를 내지 않는 유니버스 밖 종목, 전년 적자라 성장률이 없는 종목 등)은 결과에서 빠집니다`;
}

/**
 * 스크리너 결과의 **데이터 기준 시각** 한 줄 (docs/infra.md 25.364, CLAUDE.md "데이터 기준 시각 표시").
 * 예전에는 "시총 1.2조, CAGR 12.3" 이 날짜 없이 나왔다 — 월요일 스냅샷을 금요일에 봐도 알 수 없었다.
 * 종가 날짜는 종목마다 다를 수 있어 범위로 적는다. 재무는 종목마다 사업연도가 달라 가장 흔한 해를 적는다.
 */
export function screenerBasisLine(rows: ScreenerRow[]): string | null {
  if (rows.length === 0) return null;
  const parts: string[] = [];
  const snap = rows[0].snapshot_date;
  if (snap) parts.push(`시총·거래대금 ${snap} 스냅샷`);
  const prices = rows.map((r) => r.price_date).filter((d): d is string => Boolean(d)).sort();
  if (prices.length) {
    const lo = prices[0];
    const hi = prices[prices.length - 1];
    parts.push(lo === hi ? `종가 ${hi}` : `종가 ${lo}~${hi}`);
  }
  const m = rows[0].metrics_asof;
  if (m) parts.push(`성과 지표 ${m} 계산`);
  // **감성 기준일도 이 줄에** (docs/infra.md 25.499, 웹 감사). 칸의 title 로만 있어 폰에서는 볼 수 없었다.
  // 감성은 종목마다 마지막 수집일이 달라 종가처럼 범위로 적는다
  // **점수가 있는 행의 날짜만** (25.502, 교차검증). 기사가 적으면 점수는 비고 날짜만 있는 행이 온다(25.413) — 그 날짜를
  // 모으면 "—" 칸의 날짜로 범위가 늘어났다
  const 감성 = rows.filter((r) => r.sentiment != null).map((r) => r.sentiment_date)
    .filter((d): d is string => Boolean(d)).sort();
  if (감성.length) {
    const lo = 감성[0];
    const hi = 감성[감성.length - 1];
    parts.push(lo === hi ? `감성 ${hi}` : `감성 ${lo}~${hi}`);
  }
  const years = rows.map((r) => r.fiscal_year).filter((y): y is number => typeof y === "number");
  if (years.length) {
    const count = new Map<number, number>();
    for (const y of years) count.set(y, (count.get(y) ?? 0) + 1);
    const top = [...count.entries()].sort((a, b) => b[1] - a[1] || b[0] - a[0])[0][0];
    parts.push(`재무 주로 ${top} 사업보고서`);
  }
  const fa = rows[0].factors_asof;
  if (fa) parts.push(`비율 ${fa} 점수 기준`);
  // 비율은 배치 점수에서 온다 — 점수를 내지 않는 종목(유니버스 밖 등)은 비율이 빈다. "유니버스만" 을 끄면 섞이므로 수를 말한다
  // (docs/infra.md 25.491, 교차검증: 25.490 전에는 웹이 나눠 값이 있었다)
  const 비율없음 = rows.filter((r) => 점수_비율.every((k) => r[k] == null)).length;
  if (fa && 비율없음) parts.push(`비율 없는 ${비율없음}종목(점수를 내지 않는 종목 — 유니버스 밖 등)`);
  return parts.length ? `기준: ${parts.join(" · ")}` : null;
}


/**
 * 결과가 상한만큼이면 **잘렸을 수 있다**고 말한다 (docs/infra.md 25.771, 스크리너 감사). 예전에는 "100종목" 만 보여, 전체가 100 인지
 * 상한에 걸려 잘린 것인지 알 수 없었다. 0건일 때의 상한 안내(25.363)와 짝이다.
 */
export function limitNote(count: number, limit: number | null): string | null {
  if (limit === null || count < limit) return null;
  return `상한 ${limit}종목에 걸렸습니다 — 조건에 맞는 종목이 더 있을 수 있습니다(상한을 올리거나 조건을 좁히세요)`;
}
