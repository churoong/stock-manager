/**
 * 증권사 적중률 한 줄 (docs/brokers.md, docs/infra.md 25.995). 계산은 배치(`batch/services/broker_stats.py`)가 하고
 * 여기서는 글로만 옮긴다(CLAUDE.md "웹앱은 계산하지 않는다"). 문턱은 배치 `MIN_N` 과 같아야 한다.
 */

/** 성적을 보이는 최소 표본(60거래일이 지난 상향 목표가 의견 수) — batch/services/broker_stats.MIN_N */
export const BROKER_MIN_N = 20;

export interface BrokerStat {
  broker: string;
  n_fwd: number;
  avg_excess_pct: number | null;
  hit_pct: number | null;
  n_touch: number;
  touch_pct: number | null;
  avg_upside_pct: number | null;
  as_of: string;
}

export function brokerLine(s: BrokerStat | undefined): string {
  if (!s) return "성적 없음(다음 금요일 계산)";
  if ((s.n_fwd ?? 0) < BROKER_MIN_N || s.avg_excess_pct === null || s.hit_pct === null) {
    return `표본 부족(익은 의견 ${s.n_fwd ?? 0}건 < ${BROKER_MIN_N})`;
  }
  const parts = [`60거래일 초과수익 평균 ${s.avg_excess_pct > 0 ? "+" : ""}${s.avg_excess_pct.toFixed(1)}%p · 맞힘 ${s.hit_pct.toFixed(0)}% (${s.n_fwd}건)`];
  if (s.touch_pct !== null) parts.push(`목표가 터치 ${s.touch_pct.toFixed(0)}% (${s.n_touch}건)`);
  if (s.avg_upside_pct !== null) parts.push(`제시 상승여력 평균 ${s.avg_upside_pct > 0 ? "+" : ""}${s.avg_upside_pct.toFixed(0)}%`);
  return parts.join(" · ");
}
