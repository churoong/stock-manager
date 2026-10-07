/**
 * 위성 ETF 조회 (docs/etf.md 10장).
 *
 * 판정·순위는 batch/services/etf_satellite.py 가 저장했다. 웹은 읽어 묶기만 한다.
 */

import type { Country } from "@/lib/market";

export const SATELLITE_GROUPS = ["배당", "업종", "테마"] as const;

/** 하위 묶음마다 보여줄 개수. 테마는 하위 묶음이 많아 적게 둔다. */
export const TOP_PER_SUBGROUP: Record<string, number> = { 배당: 3, 업종: 4, 테마: 2 };

/** "왜 없나" 목록 길이 (묶음마다, 순자산 큰 순) */
export const SATELLITE_EXCLUDED_LIMIT = 6;

export interface SatelliteRow {
  etf_id: number;
  symbol: string;
  name: string;
  country: string;
  sat_group: string;
  sub_group: string;
  passed: number;
  excluded_reason: string | null;
  score: number | null;
  rank_in_group: number | null;
  group_size: number | null;
  rationale_text: string;
  rationale_data: string | null;
  as_of_date: string;
  expense_ratio: number | null;
  total_assets: number | null;
  premium_abs_avg: number | null;
  family: string | null;
  /** 판정 때 받은 직전 종가와 통화 (25.894) — 현재가가 아니다 */
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
  sp.sat_group, sp.sub_group, sp.passed, sp.excluded_reason, sp.score, sp.rank_in_group, sp.group_size,
  sp.rationale_text, sp.rationale_data, sp.as_of_date,
  pf.expense_ratio, pf.total_assets, pf.premium_abs_avg, pf.family, pf.prev_close, pf.currency,
  e.stock_id, px.close AS last_close, px.date AS last_close_date
FROM etf_satellite_picks sp
JOIN etfs e ON e.id = sp.etf_id
LEFT JOIN etf_profiles pf ON pf.etf_id = sp.etf_id AND pf.as_of_date = sp.as_of_date
-- **이은 ETF 의 가장 새 종가** (docs/infra.md 25.896). 이은 ETF(etfs.stock_id)만 값이 있다 — 종목마다 색인으로 한 행
LEFT JOIN prices px ON px.stock_id = e.stock_id
 AND px.date = (SELECT MAX(p5.date) FROM prices p5 WHERE p5.stock_id = e.stock_id)`;

/**
 * 그 나라의 최신 기준일, **그 날의** 가장 새 계산 버전. 핵심 조회(web/lib/etf.ts)와 같은 원칙이다.
 *
 * 버전을 전체에서 잡으면 한쪽 시장만 재판정했을 때 다른 쪽이 통째로 빠진다
 * (2026-09-22, docs/infra.md 25.111).
 */
const LATEST = `sp.as_of_date = (
    SELECT MAX(sp2.as_of_date) FROM etf_satellite_picks sp2 JOIN etfs e2 ON e2.id = sp2.etf_id
    WHERE e2.country = ?)
  AND sp.calc_version = (
    SELECT MAX(sp3.calc_version) FROM etf_satellite_picks sp3 JOIN etfs e3 ON e3.id = sp3.etf_id
    WHERE e3.country = ? AND sp3.as_of_date = (
      SELECT MAX(sp4.as_of_date) FROM etf_satellite_picks sp4 JOIN etfs e4 ON e4.id = sp4.etf_id WHERE e4.country = ?))`;
// **버전 하위 질의는 바깥 행을 가리키지 않는다** (docs/infra.md 25.858, 감사) — 행마다 다시 돌던(N²) 것을 나라 인자로 한 번만. 인자 둘이 붙는다

export function buildSatellitePassedQuery(country: Country): { sql: string; args: string[] } {
  return {
    sql: `${SELECT}
WHERE e.country = ? AND ${LATEST} AND sp.passed = 1
ORDER BY sp.sat_group, sp.sub_group, sp.rank_in_group`,
    args: [country, country, country, country],
  };
}

export function buildSatelliteExcludedQuery(country: Country): { sql: string; args: Array<string | number> } {
  return {
    sql: `${SELECT}
WHERE e.country = ? AND ${LATEST} AND sp.passed = 0
ORDER BY pf.total_assets DESC NULLS LAST`,
    args: [country, country, country, country],
  };
}

export interface SubGroup {
  name: string;
  total: number;
  rows: SatelliteRow[];
}

export interface SatelliteGroup {
  group: string;
  total: number;
  subGroups: SubGroup[];
  excluded: SatelliteRow[];
}

/**
 * 묶음 → 하위 묶음 → 순위.
 *
 * 하위 묶음은 통과 개수가 많은 순으로 놓는다. 선택지가 많은 것이 먼저 보인다.
 * 점수는 하위 묶음 안에서만 뜻이 있어 하위 묶음끼리 비교하지 않는다.
 */
export function groupSatellites(passed: SatelliteRow[], excluded: SatelliteRow[]): SatelliteGroup[] {
  return SATELLITE_GROUPS.map((group) => {
    const inGroup = passed.filter((r) => r.sat_group === group);
    const map = new Map<string, SatelliteRow[]>();
    for (const row of inGroup) map.set(row.sub_group, [...(map.get(row.sub_group) ?? []), row]);
    const top = TOP_PER_SUBGROUP[group] ?? 3;
    const subGroups = [...map.entries()]
      .map(([name, rows]) => ({
        name,
        total: rows.length,
        rows: [...rows].sort((a, b) => (a.rank_in_group ?? 1e9) - (b.rank_in_group ?? 1e9)).slice(0, top),
      }))
      .sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));
    return {
      group,
      total: inGroup.length,
      subGroups,
      excluded: excluded.filter((r) => r.sat_group === group).slice(0, SATELLITE_EXCLUDED_LIMIT),
    };
  });
}

/** rationale_data.warnings. 배치가 저장한 문구를 그대로 쓴다(docs/etf.md 10.4). */
export function parseWarnings(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const list = JSON.parse(raw)?.warnings;
    return Array.isArray(list) ? list.filter((w: unknown): w is string => typeof w === "string") : [];
  } catch {
    return [];
  }
}
