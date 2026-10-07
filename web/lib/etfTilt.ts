/**
 * 우리 점수를 많이 담은 ETF (docs/etf.md 11.2·11.3, docs/infra.md 25.967).
 *
 * 배치(`batch/jobs/etf_tilt.py`)가 판정 행의 `rationale_data.tilt` 에 N-PORT 보유 × 종합 점수 가중평균을 적었다.
 * 여기서는 읽어서 글로 옮기기만 한다 — 다시 계산하지 않는다(CLAUDE.md "웹앱은 계산하지 않는다").
 * 백테스트 전이라 보수·규모 순위와 섞지 않는 참고 순위다.
 *
 * **순위 기준은 상위 비중(T)이다** (25.968) — 우리 점수 상위 10% 종목이 ETF 비중의 몇 % 인가. 평균(A)은 넓은 바구니일수록
 * 시장 평균으로 수렴해 대표지수끼리 ±3점 안이었다. 후보도 핵심 통과(넓은 지수)만이 아니라 미국 주식형 ETF 전체다.
 */

export interface TiltContributor {
  stock_id: number;
  symbol: string;
  name: string;
  weight_pct: number;
  score: number;
}

export interface Tilt {
  avg_score: number | null;
  coverage_pct: number;
  matched_pct: number;
  total_pct: number;
  rec_pct: number | null;
  /** 우리 점수 상위 `top_share_pct`% 종목의 비중 합 (25.968) */
  top_pct: number;
  holdings: number;
  top: TiltContributor[];
  vs_market: number | null;
  benchmark: string;
  rank: number | null;
  rank_size: number | null;
  source_symbol: string;
  proxy: boolean;
  accession: string;
  filed: string;
  report_date: string | null;
  score_as_of: string | null;
  signals_as_of: string | null;
  coverage_min_pct: number;
  /** 미국 주식형 ETF 전체 안 순위(상위 비중 순). 이 기능 전 행에는 없다 */
  rank_all?: number | null;
  rank_all_size?: number | null;
  market_top_pct?: number | null;
  top_share_pct?: number;
  top_cut_score?: number | null;
  /** core = 핵심 통과(넓은 지수) · wide = 넓은 지수 아님(업종·테마·팩터) */
  pool?: "core" | "wide";
  /** us = 미국 상장 · kr_us = 국내 상장 미국 지수 · kr_kr = 국내 상장 국내 지수 (25.974). 순위는 이 무리 안에서 */
  group?: "us" | "kr_us" | "kr_kr";
  /** sec_nport · samsungfund_kodex */
  source?: string;
}

/** 배치가 적은 tilt. 없거나(이 기능 전 판정·보유 경로 없음) 깨졌으면 null — 지어내지 않는다 */
export function parseTilt(raw: string | null): Tilt | null {
  if (!raw) return null;
  try {
    const t = JSON.parse(raw)?.tilt;
    if (!t || typeof t.coverage_pct !== "number" || typeof t.accession !== "string" || !Array.isArray(t.top)) return null;
    return t as Tilt;
  } catch {
    return null;
  }
}

export const KODEX_SOURCE = "samsungfund_kodex";

/** 순위 무리 이름 (25.974) */
export const GROUP_LABEL: Record<"us" | "kr_us" | "kr_kr", string> = {
  us: "미국 주식형",
  kr_us: "국내 상장 미국 지수",
  kr_kr: "국내 상장 국내 지수",
};

const signed = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v).toFixed(0)}`;

/** 카드 한 줄. 상위 비중(순위 기준)이 먼저, 평균은 뒤. 덮은 비중이 모자라면 그 까닭을 적는다 */
export function tiltLine(t: Tilt): string {
  const 출처 = t.proxy ? ` · 같은 지수 ${t.source === KODEX_SOURCE ? `KODEX(${t.source_symbol})` : t.source_symbol} 보유로` : "";
  if (t.avg_score === null) {
    return `우리 점수 — 점수 있는 종목이 비중 ${t.coverage_pct.toFixed(0)}%뿐이라 내지 않음 (하한 ${t.coverage_min_pct.toFixed(0)}%)${출처}`;
  }
  const 상위 = `우리 상위 ${(t.top_share_pct ?? 10).toFixed(0)}% 종목 비중 ${t.top_pct.toFixed(0)}%`;
  const 배수 =
    t.market_top_pct ? ` (${t.benchmark} ${t.market_top_pct.toFixed(0)}%의 ${(t.top_pct / t.market_top_pct).toFixed(1)}배)` : "";
  // 순위는 무리 안에서 매긴 것이다 (25.970·25.974) — 다른 무리 순위로 읽히지 않게
  const 무리 = GROUP_LABEL[t.group ?? (t.proxy ? "kr_us" : "us")];
  const 전체 = t.rank_all && t.rank_all_size ? ` · ${무리} ${t.rank_all_size}개 중 ${t.rank_all}위` : "";
  const 대비 = t.vs_market === null ? "" : ` (${t.benchmark} 대비 ${signed(t.vs_market)})`;
  return `${상위}${배수}${전체} · 평균 ${t.avg_score.toFixed(0)}점${대비}${출처}`;
}

/** 넓은 지수가 아닌 후보(업종·테마·팩터)에 붙이는 경고 — 핵심 판정의 위성 경고와 같은 뜻 */
export const WIDE_POOL_WARNING =
  "넓은 지수가 아닙니다 — 한 업종·테마·전략에 몰려 있어 시장과 크게 다르게 움직일 수 있습니다. 핵심(넓은 지수)을 대신하지 않습니다";

/**
 * 국내 상장 순위를 기초지수마다 하나로 묶는다 (25.973). 같은 지수 상품은 대리 보유가 같아 값이 같다 — 나스닥100 만 9개가
 * 줄줄이 나왔다. 배치 순위가 순자산 큰 순으로 갈랐으므로 지수마다 첫 행(가장 큰 상품)을 남기고 나머지 이름을 붙인다.
 */
export function onePerIndex<R extends { category: string | null; name: string }>(rows: R[]): Array<{ row: R; others: string[] }> {
  const out: Array<{ row: R; others: string[] }> = [];
  const byIndex = new Map<string, { row: R; others: string[] }>();
  for (const row of rows) {
    const key = row.category ?? row.name;
    const seen = byIndex.get(key);
    if (seen) {
      seen.others.push(row.name);
      continue;
    }
    const item = { row, others: [] as string[] };
    byIndex.set(key, item);
    out.push(item);
  }
  return out;
}

/** 근거 한 줄 — 어느 공시·언제·점수 기준일 */
export function tiltSource(t: Tilt): string {
  const 추천 = t.rec_pct === null ? "" : ` · 장기 신호 종목 비중 ${t.rec_pct.toFixed(1)}%(${t.signals_as_of ?? "-"} 신호, 참고)`;
  const 문서 =
    t.source === KODEX_SOURCE
      ? `출처 삼성자산운용 KODEX(${t.source_symbol}) 구성종목 ${t.report_date ?? "-"}`
      : `출처 SEC N-PORT ${t.source_symbol} 공시 ${t.accession} (기준 ${t.report_date ?? "-"}, 공시 ${t.filed})`;
  return (
    문서 +
    ` · 점수 기준일 ${t.score_as_of ?? "-"} · 우리 종목으로 이은 비중 ${t.matched_pct.toFixed(0)}%${추천} · 백테스트 전 참고`
  );
}
