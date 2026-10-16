/**
 * KIS모의투자 실행 품질 — 가상 체결 판정이 얼마나 낙관적인가 (docs/paper.md 15.8, docs/desk.md 4장, docs/infra.md 25.1504).
 *
 * 배치가 날마다 `paper_broker_daily.summary_json` 에 적는 28일 창 비교 요약(체결 일치율·할인율·가격 차이)을 시장마다 계열로 읽는다.
 * 웹은 계산하지 않는다 — 그래프에 넣을 때 % 를 소수로 바꾸는 것만(그래프의 % 꼴이 소수를 받는다).
 */

import type { Series } from "@/components/EquityChart";

/** 추이를 읽는 기간(달력일) — 반년. 시장마다 하루 한 줄이라 최대 약 180행 */
export const QUALITY_DAYS = 180;

export const QUALITY_SQL = `SELECT as_of,
  json_extract(summary_json, '$.n') AS n,
  json_extract(summary_json, '$.match_pct') AS match_pct,
  json_extract(summary_json, '$.kept_pct') AS kept_pct,
  json_extract(summary_json, '$.slip_buy.avg') AS slip_buy,
  json_extract(summary_json, '$.slip_sell.avg') AS slip_sell,
  json_extract(summary_json, '$.rejected') AS rejected
FROM paper_broker_daily WHERE broker = ? AND as_of >= ? ORDER BY as_of`;

export interface QualityRow {
  as_of: string; n: number | null; match_pct: number | null; kept_pct: number | null;
  slip_buy: number | null; slip_sell: number | null; rejected: number | null;
}

/** 일치율·할인율 추이 — 끝난 주문이 없는 날(n 0·없음)은 뺀다 */
export function qualitySeries(rows: QualityRow[]): Series[] {
  const 찬 = rows.filter((r) => Number(r.n) > 0);
  const 선 = (label: string, color: string, pick: (r: QualityRow) => number | null): Series => ({
    label,
    color,
    points: 찬.flatMap((r) => {
      const v = pick(r);
      return v == null ? [] : [{ date: r.as_of, value: Number(v) / 100 }];
    }),
  });
  return [선("체결 일치율", "#2563eb", (r) => r.match_pct), 선("할인율(가상 체결이 KIS 에서도 찬 비율)", "#dc2626", (r) => r.kept_pct)].filter(
    (s) => s.points.length,
  );
}

/** 가격 차이 추이 — 불리한 쪽이 +(가상이 낙관). 표본 없는 날은 뺀다 */
export function slipSeries(rows: QualityRow[]): Series[] {
  const 선 = (label: string, color: string, pick: (r: QualityRow) => number | null): Series => ({
    label,
    color,
    points: rows.flatMap((r) => {
      const v = pick(r);
      return v == null ? [] : [{ date: r.as_of, value: Number(v) / 100 }];
    }),
  });
  return [선("매수 가격 차이", "#ea580c", (r) => r.slip_buy), 선("매도 가격 차이", "#7c3aed", (r) => r.slip_sell)].filter((s) => s.points.length);
}

/** 마지막 날 한 줄 — "지난 28일 주문 34 · 일치 82% · 할인율 90%(가상 체결 열 중 아홉이 KIS 에서도 참)" */
export function qualityLine(r: QualityRow | undefined): string {
  if (!r || !Number(r.n)) return "아직 끝난 베낀 주문이 없습니다 — 낸 다음 거래일의 같은 시장 배치가 체결을 맞춥니다.";
  const pct = (v: number | null) => (v == null ? "-" : `${Number(v).toFixed(0)}%`);
  const 할인 = r.kept_pct == null ? "할인율 모름(가상이 찬 주문 없음)" : `할인율 ${pct(r.kept_pct)}(가상 체결 가운데 KIS 에서도 찬 비율)`;
  return `${r.as_of} 기준 지난 28일 끝난 주문 ${r.n} · 체결 일치 ${pct(r.match_pct)} · ${할인}`;
}
