/**
 * ETF 장기 적립 조회.
 *
 * 규칙은 docs/etf.md 8장, 계산은 batch/services/etf.py 가 한다. 웹은 저장된
 * 판정을 읽어 보여주기만 한다(CLAUDE.md). 점수도 순위도 여기서 다시 내지 않는다.
 *
 * 오늘의 추천과 화면을 나눈 이유 (etf.md 4장)
 *   오늘의 추천은 타이밍을 보고 매수 구간을 준다. 장기 적립은 타이밍이 없다.
 *   한 화면에 두면 "이 ETF 는 매수 구간이 왜 없지" 같은 혼란이 난다.
 */

import { HORIZON_LABEL } from "@/lib/recommend";

export const BUCKET_ORDER = ["국내 주식", "미국 주식", "해외 주식", "세계 주식", "채권"] as const;

/** 시장 순서와 이름. 국내가 먼저다 — 원화로 적립하는 사용자다. */
export const MARKETS: Array<{ country: "KR" | "US"; label: string }> = [
  { country: "KR", label: "국내 상장" },
  { country: "US", label: "미국 상장" },
];

/** 분류마다 보여줄 개수. 20년 적립에서 고를 후보는 몇 개면 충분하다. */
export const TOP_PER_CATEGORY = 3;

/** "왜 없나" 목록 길이. 순자산이 큰 순이다. 이름이 알려진 ETF 가 먼저 나온다. */
export const EXCLUDED_LIMIT = 8;

/**
 * 오래된 값 문턱(일). ETF 배치는 월 1회(etf.yml)라 한 달은 정상이다.
 * 40일을 넘기면 한 주기를 건너뛴 것이다.
 */
export const ETF_STALE_AFTER_DAYS = 40;

export interface EtfRow {
  etf_id: number;
  symbol: string;
  name: string;
  country: string;
  bucket: string | null;
  passed: number;
  excluded_reason: string | null;
  score: number | null;
  rank_in_category: number | null;
  category_size: number | null;
  rationale_text: string;
  rationale_data: string | null;
  as_of_date: string;

  category: string | null;
  family: string | null;
  expense_ratio: number | null;
  total_assets: number | null;
  inception_date: string | null;
  turnover_est: number | null;
  premium_abs_avg: number | null;
  /** 판정 때 받은 직전 종가와 통화 (25.894). ETF 는 날마다 시세를 받지 않아 **현재가가 아니다** — 판정일을 붙여 보인다 */
  prev_close?: number | null;
  currency?: string | null;
  /** 이은 stocks 줄과 그 가장 새 종가 (25.896). 잇기 전이면 비어 있다 */
  stock_id?: number | null;
  last_close?: number | null;
  last_close_date?: string | null;
}

const SELECT = `
SELECT
  e.id AS etf_id, e.symbol, e.name, e.country,
  pk.bucket, pk.passed, pk.excluded_reason, pk.score, pk.rank_in_category, pk.category_size,
  pk.rationale_text, pk.rationale_data, pk.as_of_date,
  pf.category, pf.family, pf.expense_ratio, pf.total_assets, pf.inception_date, pf.turnover_est,
  pf.premium_abs_avg, pf.prev_close, pf.currency,
  e.stock_id, px.close AS last_close, px.date AS last_close_date
FROM etf_picks pk
JOIN etfs e ON e.id = pk.etf_id
LEFT JOIN etf_profiles pf ON pf.etf_id = pk.etf_id AND pf.as_of_date = pk.as_of_date
-- **이은 ETF 의 가장 새 종가** (docs/infra.md 25.896). 이은 ETF(etfs.stock_id)만 값이 있다 — 종목마다 색인으로 한 행
LEFT JOIN prices px ON px.stock_id = e.stock_id
 AND px.date = (SELECT MAX(p5.date) FROM prices p5 WHERE p5.stock_id = e.stock_id)`;

/**
 * 시장마다 가장 최근 판정, 그중 가장 새 계산 버전만 본다.
 *
 * 판정은 달마다 쌓이므로 기준일을 고정하지 않으면 지난달 것이 섞인다. 국내와 미국은
 * 받는 날이 달라 기준일도 시장별로 잡는다. 규칙을 바꿔 재판정하면 같은 기준일에
 * 계산 버전만 다른 행이 생기므로(batch/services/etf.py CALC_VERSION) 새 버전만 읽는다.
 *
 * **"그중" 은 그 시장·그 기준일 안에서다** (2026-09-22, docs/infra.md 25.111).
 * 예전에는 버전만 전체에서 `MAX` 로 잡아서, 한쪽 시장만 재판정하면 다른 쪽이 통째로 빠졌다.
 * 주석은 시장별이라고 적어 두고 SQL 은 안 그랬다.
 */
/** 한 나라의 최신 기준일·그날의 가장 새 버전. 나라는 코드의 글자(사용자 입력 아님)라 바로 적는다 — 그래야 인자 없이 한 번만 계산된다 */
const 최신 = (c: "KR" | "US") =>
  `(e.country = '${c}' AND pk.as_of_date = (SELECT MAX(pk2.as_of_date) FROM etf_picks pk2 JOIN etfs e2 ON e2.id = pk2.etf_id WHERE e2.country = '${c}') AND pk.calc_version = (SELECT MAX(pk3.calc_version) FROM etf_picks pk3 JOIN etfs e3 ON e3.id = pk3.etf_id WHERE e3.country = '${c}' AND pk3.as_of_date = (SELECT MAX(pk4.as_of_date) FROM etf_picks pk4 JOIN etfs e4 ON e4.id = pk4.etf_id WHERE e4.country = '${c}')))`;

// **버전 하위 질의는 바깥 행을 가리키지 않는다** (docs/infra.md 25.858, 감사). 예전에는 `e3.country = e.country AND pk3.as_of_date = pk.as_of_date`
// 라 행마다 다시 돌아(N²) ETF 탭 한 번에 약 157만 행을 읽었다
const LATEST = `(${최신("KR")} OR ${최신("US")})`;

/** "우리 종목 집중" 목록 길이 (docs/etf.md 11.5) */
export const TILT_LEADERS_LIMIT = 30;

/**
 * 우리 종목 집중 순위 (docs/etf.md 11.5, docs/infra.md 25.968). 미국 최신 판정 행 가운데 배치가 전체 순위(`tilt.rank_all`)를
 * 매긴 것 — **핵심 통과가 아니어도**(업종·테마·팩터) 들어온다. 순위는 배치가 낸 그대로 읽는다.
 */
export function buildTiltLeadersQuery(
  limit = TILT_LEADERS_LIMIT, country: "KR" | "US" = "US",
): { sql: string; args: Array<string | number> } {
  // 국내 상장은 국내끼리 매긴 순위다 (25.970) — 시장마다 따로 읽는다
  return {
    sql: `${SELECT}
WHERE ${최신(country)} AND json_extract(pk.rationale_data, '$.tilt.rank_all') IS NOT NULL
ORDER BY json_extract(pk.rationale_data, '$.tilt.rank_all')
LIMIT ?`,
    args: [limit],
  };
}

export function buildPickedQuery(): { sql: string; args: Array<string | number> } {
  return {
    sql: `${SELECT}
WHERE ${LATEST} AND pk.passed = 1
ORDER BY e.country, pk.bucket, pf.category, pk.rank_in_category`,
    args: [],
  };
}

/** 순자산은 통화가 달라 시장끼리 섞어 줄 세울 수 없다. 시장별로 부른다. */
export function buildExcludedQuery(
  country: "KR" | "US",
  limit = EXCLUDED_LIMIT,
): { sql: string; args: Array<string | number> } {
  return {
    sql: `${SELECT}
WHERE ${LATEST} AND pk.passed = 0 AND e.country = ?
ORDER BY pf.total_assets DESC NULLS LAST
LIMIT ?`,
    args: [country, limit],
  };
}

export interface CategoryGroup {
  category: string;
  rows: EtfRow[];
  total: number;
}

export interface BucketGroup {
  bucket: string;
  categories: CategoryGroup[];
  total: number;
}

/**
 * 한 시장에 판정 행이 하나도 없을 때의 문장 (docs/infra.md 25.486, 웹 감사). 그 시장의 판정이 **돌았는지**로 가른다 —
 * 예전에는 늘 "아직 판정 결과가 없습니다" 여서, 바로 위 "마지막 배치 09/28 08:40" 과 부딪쳤다(돌았는데 0건인 날).
 */
export function emptyMarketNote(ranAt: string | null): string {
  return ranAt
    ? `판정은 돌았는데(${ranAt} KST) 이 시장의 후보 ETF 가 없었습니다. 고장이 아니라 종목 마스터에 이 시장 ETF 가 아직 없는 것일 수 있습니다`
    : "아직 이 시장의 판정이 돈 적이 없습니다 (예약 실행이 돌면 채워집니다)";
}

/**
 * 묶음 → 분류 → 분류 안 순위.
 *
 * **점수는 분류 안에서만 뜻이 있다**(etf.md 8.3). 처음에는 묶음 안에서 점수로
 * 줄 세웠는데, 첫 시험 실행(2026-09-17)에서 IWM(보수 0.19%)이 Small Blend 에
 * 혼자라 100점을 받아 VTI(0.03%)보다 위에 섰다. 분류가 다르면 점수끼리 비교하지
 * 않는다. 분류 순서는 통과한 개수가 많은 순이다 — 선택지가 많은 분류가 먼저 보인다.
 */
export function groupByBucket(rows: EtfRow[], top = TOP_PER_CATEGORY): BucketGroup[] {
  return BUCKET_ORDER.map((bucket) => {
    const inBucket = rows.filter((r) => r.bucket === bucket);
    const byCategory = new Map<string, EtfRow[]>();
    for (const row of inBucket) {
      const key = row.category ?? "(분류 없음)";
      byCategory.set(key, [...(byCategory.get(key) ?? []), row]);
    }
    const categories = [...byCategory.entries()]
      .map(([category, list]) => ({
        category,
        total: list.length,
        rows: [...list]
          .sort((a, b) => (a.rank_in_category ?? Infinity) - (b.rank_in_category ?? Infinity))
          .slice(0, top),
      }))
      .sort((a, b) => b.total - a.total || a.category.localeCompare(b.category));
    return { bucket, categories, total: inBucket.length };
  }).filter((g) => g.total > 0);
}

export interface MarketGroup {
  country: "KR" | "US";
  label: string;
  asOf: string | null;
  total: number;
  buckets: BucketGroup[];
}

/**
 * 시장 → 묶음 → 분류. 판정이 없는 시장도 자리를 남긴다(아직 안 돌렸다는 것을 보여 주려고).
 * 기준일은 통과 행에서, 통과가 0개면 **탈락 행에서** 읽는다 (docs/infra.md 25.483) — 예전에는 판정은 돌았는데 통과가 없는 날
 * "기준일 -" 로 나와 묵음 표시까지 꺼졌다(위성 ETF 경로는 이미 이렇게 했다)
 */
export function groupByMarket(
  rows: EtfRow[], top = TOP_PER_CATEGORY, excluded: Record<string, EtfRow[]> = {},
): MarketGroup[] {
  return MARKETS.map(({ country, label }) => {
    const inMarket = rows.filter((r) => r.country === country);
    return {
      country,
      label,
      asOf: inMarket[0]?.as_of_date ?? excluded[country]?.[0]?.as_of_date ?? null,
      total: inMarket.length,
      buckets: groupByBucket(inMarket, top),
    };
  });
}

export interface OverlapMatch {
  symbol: string;
  name: string;
  pct: number;
  horizon: string;
}

export interface Overlap {
  available: boolean;
  basis: string;
  as_of: string | null;
  note?: string;
  matched?: OverlapMatch[];
  total_pct?: number;
}

/**
 * 추천 종목 겹침. 배치가 rationale_data.overlap 에 저장했다(etf.md 8.4).
 * 없거나 깨졌으면 null. 화면은 null 이면 줄을 그리지 않는다.
 */
export function parseOverlap(raw: string | null): Overlap | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw)?.overlap;
    if (!parsed || typeof parsed.available !== "boolean" || typeof parsed.basis !== "string") {
      return null;
    }
    return parsed as Overlap;
  } catch {
    return null;
  }
}

/**
 * 겹침 한 줄. "모른다" 와 "0%" 를 구분해서 적는다.
 *
 * **"오늘" 이라고 쓰지 않는다** (docs/infra.md 25.353). 겹침은 월 1회 ETF 배치가 **그때의 신호**(`as_of`)로 계산해
 * 저장한 것이다. 9월 1일 배치를 9월 27일에 보면 "오늘 추천 종목" 은 거짓이다. 기준일을 붙인다.
 */
export function overlapText(overlap: Overlap | null): string | null {
  if (!overlap) return null;
  if (!overlap.available) return overlap.note ?? "추천 종목 겹침을 계산하지 않았습니다";
  const matched = overlap.matched ?? [];
  const 기준 = overlap.as_of ? ` (${overlap.as_of} 신호 기준)` : "";
  if (matched.length === 0) {
    return `${overlap.basis} 중 추천 종목 없음${기준}`;
  }
  const list = matched
    .map((m) => `${m.symbol} ${formatPct(m.pct)} (${(HORIZON_LABEL[m.horizon] ?? m.horizon).split(" ")[0]})`)
    .join(", ");
  return `${overlap.basis} 중 추천 종목 ${formatPct(overlap.total_pct ?? 0)} 이상: ${list}${기준}`;
}

/** 시장 통화로 순자산을 적는다. 국내는 조원·억원, 미국은 $B. */
export function formatAssets(value: number | null | undefined, country: string): string {
  return country === "KR" ? formatKrw(value) : formatUsd(value);
}

/**
 * 24715731480706 → "24.72조원". 배치의 fmt_krw 와 같은 단위·자릿수 규칙이다.
 * 단, **딱 반인 값의 반올림은 다르다** (docs/infra.md 25.282): 2.5억 → 배치(파이썬 서식, 짝수 쪽) "2억원", 여기(Math.round, 위쪽) "3억원".
 * 순자산이 정확히 0.5억 단위로 떨어질 때만 일어나고 한 칸 차이라 맞추지 않았다.
 */
export function formatKrw(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  if (Math.abs(value) >= 1e12) return `${(value / 1e12).toFixed(2).replace(/.00$/, "")}조원`;
  if (Math.abs(value) >= 1e8) return `${Math.round(value / 1e8).toLocaleString("ko-KR")}억원`;
  return `${Math.round(value).toLocaleString("ko-KR")}원`;
}

/** 0.0003 → "0.03%". 표시만 한다. 값은 배치가 저장한 그대로다. */
export function formatPct(ratio: number | null | undefined, digits = 2): string {
  if (ratio === null || ratio === undefined) return "-";
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** 1.76e12 → "$1.76T". 자릿수를 읽기 쉽게. 배치의 fmt_usd 와 같은 단위 규칙 — 딱 반인 값의 반올림만 다르다(formatKrw 주석). */
export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  const units: Array<[string, number]> = [
    ["T", 1e12],
    ["B", 1e9],
    ["M", 1e6],
  ];
  for (const [unit, size] of units) {
    if (Math.abs(value) >= size) {
      return `$${(value / size).toFixed(2).replace(/\.00$/, "")}${unit}`;
    }
  }
  return `$${Math.round(value).toLocaleString("en-US")}`;
}

/** 그 시장의 마지막 **실패한** ETF 판정 (25.826) */
export const ETF_LAST_FAILED = `SELECT started_at, error_text FROM batch_runs
WHERE job_name = 'etf' AND market = ? AND status = 'failed' ORDER BY started_at DESC LIMIT 1`;

/**
 * 마지막 판정이 **실패**했고 마지막 성공보다 뒤면 한 줄 (docs/infra.md 25.826, 감사). 화면은 success·partial 만 읽어, 첫 조각부터 막혀 failed 로
 * 끝난 달(25.614)에는 지난 판정을 까닭 없이 보였다 — 40일이 지나야 노란 표시가 떴다. `lastOkFinished` 는 마지막 성공·일부 성공의 끝난 시각
 */
export function etfFailedNote(
  failed: { started_at: unknown; error_text: unknown } | undefined, lastOkFinished: unknown,
): string | null {
  if (!failed?.started_at) return null;
  if (lastOkFinished && String(failed.started_at) <= String(lastOkFinished)) return null;
  const 까닭 = failed.error_text ? String(failed.error_text).slice(0, 160) : "까닭 기록 없음";
  return `가장 최근 ETF 판정이 실패했습니다(${String(failed.started_at).slice(0, 10)} 시작: ${까닭}) — 아래는 지난 판정입니다`;
}

/**
 * 마지막 ETF 배치가 **일부만 끝났을 때** 화면에 붙이는 한 줄 (docs/infra.md 25.580, 감사).
 *
 * `status` 를 읽고도 버려서, 3년 전 목록을 못 받아 모든 국내 ETF 가 "운용 3년 미만" 으로 떨어진 판이나 야후 차단으로
 * 일부만 받은 판이 **깨끗한 결과처럼** 보였다. 배치가 `step_log.notes` 에 남긴 까닭을 그대로 싣는다(웹은 만들지 않는다).
 * `step_log` 를 못 읽으면 못 읽었다고 말한다 — 까닭이 없다고 하지 않는다.
 */
export function etfRunNote(run: { status: unknown; step_log?: unknown } | null | undefined): string | null {
  if (!run || run.status !== "partial") return null;
  let 까닭: string[] | null = null;
  try {
    const log = typeof run.step_log === "string" ? JSON.parse(run.step_log) : null;
    if (log && Array.isArray(log.notes)) 까닭 = log.notes.filter((n: unknown): n is string => typeof n === "string");
  } catch {
    까닭 = null;
  }
  const 앞 = "마지막 판정이 일부만 끝났습니다";
  if (까닭 === null) return `${앞} — 까닭(실행 기록)을 읽지 못했습니다. /status 에서 봅니다`;
  // "저장하지 않았다" 는 **맨 앞에** — 잘리면 화면이 지난 판정인 줄 모르고 "모두 불통과" 로 읽었다 (25.583, 교차검증)
  const 차례 = [...까닭.filter((n) => n.includes("저장하지 않았습니다")), ...까닭.filter((n) => !n.includes("저장하지 않았습니다"))];
  return 차례.length ? `${앞}: ${차례.slice(0, 3).join(" · ")}` : 앞;
}

/**
 * 이름으로 레버리지·인버스라 보고 **판정 전에** 뺀 미국 ETF (docs/infra.md 25.718, 감사). 판정·저장을 하지 않아 "왜 없나" 표에도
 * 없었다 — 실행 기록(`step_log.name_excluded`)에서 읽어 한 줄로 알린다. 못 읽으면 말하지 않는다(다른 줄이 실패를 알린다)
 */
// SH·PSQ 는 이름("ProShares Short …")이 이름 거르기에 걸리지 않아 이 목록에 나올 수 없다 — 뺐다 (25.761, ETF 감사)
const 잘_알려진 = ["TQQQ", "SQQQ", "SOXL", "SOXS", "UPRO", "SPXU", "TNA", "TZA"];

export function etfNameExcludedNote(step_log: unknown): string | null {
  try {
    const log = typeof step_log === "string" ? JSON.parse(step_log) : null;
    const 목록 = log && Array.isArray(log.name_excluded) ? log.name_excluded.filter((x: unknown) => typeof x === "string") : [];
    if (!목록.length) return null;
    // 잘 알려진 상품을 먼저 보인다 — 알파벳 앞쪽 8개(AAPB…)는 "왜 TQQQ 가 없나" 에 답이 안 됐다 (25.722, 교차검증)
    const 앞 = 잘_알려진.filter((x) => 목록.includes(x));
    const 예 = [...앞, ...목록.filter((x: string) => !앞.includes(x))].slice(0, 8);
    return `이름으로 레버리지·인버스라 보고 판정 전에 뺀 ETF ${목록.length}개 (예: ${예.join(", ")})`;
  } catch {
    return null;
  }
}

/** 그 실행이 판정을 저장하지 않았나 — 화면은 지난 판정이다 (25.583) */
export function etfRunNotStored(note: string | null | undefined): boolean {
  return Boolean(note?.includes("저장하지 않았습니다"));
}

