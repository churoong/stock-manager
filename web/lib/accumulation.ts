/**
 * 장기 적립 종목 조회 (docs/accumulation.md).
 *
 * 판정·순위는 batch/services/accumulation.py 가 저장했다. 웹은 읽어 보여 주기만 한다.
 * 국내·미국은 규칙이 조금 다르다(7장). 나라마다 따로 읽고 따로 줄 세운다.
 */

import type { Country } from "@/lib/market";

/** 화면 머리 고정 경고. 문구의 단일 정의처는 docs/accumulation.md 5장이다. */
export const ACCUMULATION_WARNINGS = [
  "개별 종목은 대부분 장기적으로 시장에 못 미칩니다. ETF 핵심을 먼저 채우세요 (Bessembinder 2018)",
  "재무 5개 사업연도뿐이라 10~20년 적합성은 검증하지 않았습니다",
  "상장폐지 종목은 데이터에 없어 통과율이 좋아 보입니다 (생존편향)",
] as const;

/**
 * 네 번째 고정 경고 — **상한은 설정값이다** (docs/infra.md 25.255). 예전에는 "한 종목 10%·업종 30%" 를 글자로 박아,
 * 설정에서 상한을 5%·20% 로 바꿔도 이 화면만 옛 숫자를 권했다(리포트·포트폴리오는 설정을 따른다).
 */
export function sectorCapWarning(stockPct: number, sectorPct: number): string {
  return `업종 분산을 확인할 수 없습니다. 한 종목 ${stockPct}%·업종 ${sectorPct}% 상한(설정)을 직접 지키세요`;
}

/** 미국에만 붙는 경고 (7장). 규칙이 국내와 달라진 곳을 숨기지 않는다. */
export const ACCUMULATION_WARNINGS_US = [
  "상장일 대신 연간 재무(10-K) 10년 연속 제출로 이력을 봤습니다",
  "영업이익 줄이 없는 회사(JNJ·CVX 등)는 그해 세전이익으로 흑자를 판정했습니다",
  "은행·보험은 부채비율 조건에서 빠집니다. 금융주 적립 후보는 없습니다",
] as const;

/** 게이트 이름. 퍼널 표에서 "어디서 몇 개가 빠졌나" 를 읽게 한다 (2장). */
export const GATE_LABELS: Record<string, string> = {
  G1: "유니버스 편입",
  G2: "상장 10년",
  G3: "연결 재무 5개년",
  G4: "금융업 제외(대리)",
  G5: "5년 연속 영업흑자",
  G6: "적자·자본잠식 없음",
  G7: "부채비율 ≤ 100%",
  G8: "5년 평균 ROE ≥ 중앙값",
  G9: "시총 400위 이내",
  G10: "5년 연속 현금배당",
  // 관문이 아니다. 근거표를 만들지 못해 추천에서 뺀 것 (docs/infra.md 25.174).
  // **0 이어야 정상이다.** 여기 숫자가 보이면 근거표를 만드는 쪽이 고장 난 것이다
  // (batch/services/accumulation.NO_CRITERIA_GATE 와 같은 이름이어야 한다)
  근거: "근거표를 만들지 못함 (0이어야 정상)",
};

/** 미국에서 이름이 달라지는 게이트 (7장). G4 는 미국에서 판정하지 않아 퍼널에 나오지 않는다. */
const GATE_LABELS_US: Record<string, string> = {
  G2: "연간 재무 10년 연속",
  G5: "5년 연속 흑자 (영업이익 없으면 세전이익)",
  G9: "시총 500위 이내",
};

export function gateLabel(gate: string, country: Country): string {
  return (country === "US" ? GATE_LABELS_US[gate] : undefined) ?? GATE_LABELS[gate] ?? gate;
}

/** 재무·배당은 연 1회 바뀐다. **판정 배치**가 두 달 넘게 안 돌았으면 노랗게. */
export const ACCUMULATION_STALE_AFTER_DAYS = 60;

/**
 * 근거표 **한 줄 한 줄**의 나이 문턱. 위 값과 **다른 것을 잰다** —
 * 위는 "판정이 언제 돌았나", 이것은 "그 줄이 인용한 수치가 언제 것인가" 다.
 *
 * 이 화면의 근거는 전부 **사업보고서**에서 온다. 사업보고서는 결산 뒤 90일 안에
 * 나오므로(12월 결산이면 이듬해 3월), 다음 해 보고서가 들어오기 전까지 가장 오래된
 * 줄은 **13개월쯤** 된다. 400일은 거기에 조금 얹은 값이다.
 *
 * 60 을 그대로 쓰면 **정상인 연간 수치가 전부 노랗게** 뜬다 — 늑대야 하고 외치는 것이다.
 * 2026-09-21 까지 이 값은 컴포넌트에 **400 이라고 손으로 박혀** 있었고, 그래서 왜
 * 400 인지 아무 데도 적혀 있지 않았다 (docs/infra.md 25.94).
 */
export const ACCUMULATION_CRITERIA_STALE_AFTER_DAYS = 400;

export interface AccumulationRow {
  stock_id: number;
  ticker: string;
  name_ko: string | null;
  market: string;
  as_of_date: string;
  fiscal_year_to: number;
  score: number | null;
  rank_in_group: number | null;
  group_size: number | null;
  long_signal_on: number;
  rationale_text: string;
  rationale_data: string | null;
  /** 종목 통화와 **가장 새 종가**·그 날짜 (docs/infra.md 25.894). 판정은 월 1회라 판정일과 다르다 — 날짜를 붙여 보인다 */
  currency?: string | null;
  close?: number | null;
  price_date?: string | null;
}

export interface FunnelStep {
  gate: string;
  label: string;
  failed: number;
}

/**
 * 그 나라의 최신 판정일, **그 날의** 가장 새 계산 버전.
 *
 * 나라마다 배치가 따로 돌아 날짜가 다를 수 있다 — 그래서 기준일은 나라별로 잡는다.
 * **계산 버전도 같다** (2026-09-22, docs/infra.md 25.111). 예전에는 버전만
 * `MAX(calc_version)` 으로 **전체**에서 잡았다. 국내가 새 버전으로 다시 돌고 미국이
 * 못 돌면(실패·건너뜀), 미국은 제 기준일의 옛 버전 행만 있는데 조건은 새 버전을 요구해
 * **한 줄도 안 나온다.** 화면은 "아직 판정이 없다" 처럼 보이고 아무도 이유를 모른다.
 */
const LATEST = `p.as_of_date = (
    SELECT MAX(p2.as_of_date) FROM stock_accum_picks p2 JOIN stocks s2 ON s2.id = p2.stock_id WHERE s2.country = ?)
  AND p.calc_version = (
    SELECT MAX(p3.calc_version) FROM stock_accum_picks p3 JOIN stocks s3 ON s3.id = p3.stock_id
    WHERE s3.country = ? AND p3.as_of_date = (
      SELECT MAX(p4.as_of_date) FROM stock_accum_picks p4 JOIN stocks s4 ON s4.id = p4.stock_id WHERE s4.country = ?))`;
// **버전 하위 질의는 바깥 행을 가리키지 않는다** (docs/infra.md 25.858, 감사). 예전에는 `s3.country = s.country AND p3.as_of_date = p.as_of_date` 라
// 행마다 다시 돌아(N²) 적립 종목 퍼널 한 번에 국내 2,760행이면 약 1,500만 행을 읽었다. 나라를 인자로 받아 한 번만 계산한다 — 인자 셋(나라)이 붙는다

/** 판정은 월 1회라 그 뒤 폐지·제외된 종목이 한 달 동안 적립 후보로 보였다 — 지금 상태로 거른다 (docs/infra.md 25.824, 감사) */
export function buildAccumulationPassedQuery(country: Country): { sql: string; args: string[] } {
  return {
    sql: `SELECT p.stock_id, s.ticker, COALESCE(s.name_ko, s.name_en) AS name_ko, s.market, p.as_of_date, p.fiscal_year_to, p.score,
  p.rank_in_group, p.group_size, p.long_signal_on, p.rationale_text, p.rationale_data,
  s.currency, px.close, px.date AS price_date
FROM stock_accum_picks p
JOIN stocks s ON s.id = p.stock_id
-- **가장 새 종가** (docs/infra.md 25.894, 사용자 요청: 적립 탭에도 현재가). 추천·종목 찾기와 같은 모양 —
-- 종목마다 (stock_id, date) 색인으로 한 행만 찾는다(25.586). 통과 종목은 수십 개라 읽는 행도 그만큼이다.
-- 이 SQL 의 주석에 물음표를 쓰지 않는다 — 자리표시자로 세어진다
LEFT JOIN prices px ON px.stock_id = s.id
 AND px.date = (SELECT MAX(p5.date) FROM prices p5 WHERE p5.stock_id = s.id)
WHERE s.country = ? AND ${LATEST} AND p.passed = 1 AND s.status = 'active'
ORDER BY p.rank_in_group`,
    args: [country, country, country, country],
  };
}

/** 판정 때 통과했는데 지금 활성이 아닌 종목 수 — 통과 목록(활성만)과 퍼널(탈락만) 어디에도 안 잡혀 "유니버스 N종목" 이 줄었다 (25.912) */
export function buildAccumulationGoneQuery(country: Country): { sql: string; args: string[] } {
  return {
    sql: `SELECT COUNT(*) AS n
FROM stock_accum_picks p
JOIN stocks s ON s.id = p.stock_id
WHERE s.country = ? AND ${LATEST} AND p.passed = 1 AND s.status <> 'active'`,
    args: [country, country, country, country],
  };
}

export function buildAccumulationFunnelQuery(country: Country): { sql: string; args: string[] } {
  return {
    sql: `SELECT p.first_failed_gate AS gate, COUNT(*) AS n, MAX(p.as_of_date) AS as_of
FROM stock_accum_picks p
JOIN stocks s ON s.id = p.stock_id
WHERE s.country = ? AND ${LATEST} AND p.passed = 0
GROUP BY p.first_failed_gate`,
    args: [country, country, country, country],
  };
}

/** 나스닥 목록 이름의 꼬리를 뗀다. batch/services/accumulation.display_name 과 같은 규칙. */
export function displayName(name: string | null): string | null {
  if (!name) return name;
  for (const tail of [" - Common Stock", " Class A Common Stock", " Common Stock", " Common Shares", " - Class A"]) {
    if (name.endsWith(tail)) return name.slice(0, -tail.length).replace(/[ -]+$/, "");
  }
  return name;
}

/**
 * 관문 번호 순서. 없으면 null.
 *
 * **`Number(gate.slice(1))` 을 그대로 쓰지 않는다** (2026-09-23, docs/infra.md 25.175).
 * 관문이 아닌 칸(`근거`)이 하나라도 섞이면 `Number("거")` 가 NaN 이 되고, NaN 이 낀
 * 비교 함수는 늘 NaN 을 돌려줘 **정렬 전체가 무너진다** — G10 이 G2 앞에 온다.
 * 한 줄이 잘못 놓이는 것이 아니라 표 전체가 뒤섞인다.
 */
function gateOrder(gate: string): number | null {
  const m = /^G(\d+)$/.exec(gate);
  return m ? Number(m[1]) : null;
}

/** G1 → G10 순서로, 관문이 아닌 칸(`근거`)은 맨 뒤에. 사전순이면 G10 이 G2 앞에 온다. */
export function orderFunnel(rows: Array<{ gate: string | null; n: number }>, country: Country = "KR"): FunnelStep[] {
  return rows
    .filter((r): r is { gate: string; n: number } => typeof r.gate === "string")
    .map((r) => ({ gate: r.gate, label: gateLabel(r.gate, country), failed: Number(r.n) }))
    .sort((a, b) => {
      const x = gateOrder(a.gate);
      const y = gateOrder(b.gate);
      if (x === null && y === null) return a.gate.localeCompare(b.gate);
      if (x === null) return 1;
      if (y === null) return -1;
      return x - y;
    });
}

/** 근거표의 "배당 (참고)" 행 값. 카드 요약 줄에 그대로 쓴다. */
export function dividendSummary(raw: string | null): string | null {
  if (!raw) return null;
  try {
    const list = JSON.parse(raw)?.criteria;
    if (!Array.isArray(list)) return null;
    const row = list.find((c: { label?: unknown }) => c?.label === "배당 (참고)");
    return typeof row?.display === "string" ? row.display : null;
  } catch {
    return null;
  }
}
