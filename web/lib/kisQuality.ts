/**
 * KIS모의투자 실행 품질 — 가상 체결 판정이 얼마나 낙관적인가 (docs/paper.md 15.8, docs/desk.md 4장, docs/infra.md 25.1504).
 *
 * 배치가 날마다 `paper_broker_daily.summary_json` 에 적는 28일 창 비교 요약(체결 일치율·실현율·가격 차이)을 시장마다 계열로 읽는다.
 * 웹은 계산하지 않는다 — 그래프에 넣을 때 % 를 소수로 바꾸는 것만(그래프의 % 꼴이 소수를 받는다).
 */

import type { Series } from "@/components/EquityChart";

/**
 * 괴리 알림 문턱 — `batch/services/broker_mirror.py` 의 DIVERGE_* 와 같은 값(테스트가 대조, 25.1507). 화면 설명 글에만 쓴다
 */
export const DIVERGE = { MATCH_PCT: 80, KEPT_PCT: 85, SLIP_PCT: 0.5, MIN_N: 20, COMPARE_DAYS: 28 } as const;

/** 추이를 읽는 기간(달력일) — 반년. 시장마다 하루 한 줄이라 최대 약 180행 */
export const QUALITY_DAYS = 180;

export const QUALITY_SQL = `SELECT as_of,
  json_extract(summary_json, '$.n') AS n,
  json_extract(summary_json, '$.match_pct') AS match_pct,
  json_extract(summary_json, '$.kept_pct') AS kept_pct,
  json_extract(summary_json, '$.slip_buy.avg') AS slip_buy,
  json_extract(summary_json, '$.slip_sell.avg') AS slip_sell,
  json_extract(summary_json, '$.rejected') AS rejected,
  json_extract(summary_json, '$.unknown') AS unknown
FROM paper_broker_daily WHERE broker = ? AND as_of >= ? ORDER BY as_of`;

export interface QualityRow {
  as_of: string; n: number | null; match_pct: number | null; kept_pct: number | null;
  slip_buy: number | null; slip_sell: number | null; rejected: number | null; unknown?: number | null;
}

/** 일치율·실현율 추이 — 끝난 주문이 없는 날(n 0·없음)은 뺀다 */
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
  return [선("체결 일치율", "#2563eb", (r) => r.match_pct), 선("실현율(가상 체결이 KIS 에서도 찬 비율)", "#dc2626", (r) => r.kept_pct)].filter(
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

/**
 * 마지막 날 한 줄 — "지난 28일 끝난 주문 34 · 체결 일치 82% · 실현율 90%(…) · 모름 2건(빠짐)".
 * 25.1504 에는 "할인율" 이라 불렀다 — 100 이 "깎을 것 없음" 이라 이름이 뜻과 반대였다(25.1507, 검토 3).
 * 모름(미국 예약이 개장 때 전송 안 됨 등)은 일치·실현율에서 빠져 실현율을 높게 기울인다 — 건수를 따로 보인다(검토 6)
 */
export function qualityLine(r: QualityRow | undefined): string {
  if (!r || !Number(r.n)) return "아직 끝난 베낀 주문이 없습니다 — 낸 다음 거래일의 같은 시장 배치가 체결을 맞춥니다.";
  const pct = (v: number | null) => (v == null ? "-" : `${Number(v).toFixed(0)}%`);
  const 실현 = r.kept_pct == null ? "실현율 모름(가상이 찬 주문 없음)" : `실현율 ${pct(r.kept_pct)}(가상 체결 가운데 KIS 에서도 찬 비율)`;
  const 모름 = Number(r.unknown) > 0 ? ` · 모름 ${r.unknown}건(일치·실현율에서 빠짐)` : "";
  return `${r.as_of} 기준 지난 ${DIVERGE.COMPARE_DAYS}일 끝난 주문 ${r.n} · 체결 일치 ${pct(r.match_pct)} · ${실현}${모름}`;
}
