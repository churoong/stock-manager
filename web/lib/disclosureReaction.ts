/**
 * 공시 반응 통계 한 줄 (docs/disclosure_reaction.md, docs/infra.md 25.996). 계산은 배치(`batch/services/disclosure_reaction.py`)가
 * 하고, 유형을 가르는 낱말도 배치가 표(`disclosure_reaction.keywords`)에 함께 적는다 — 규칙을 두 언어로 따로 적지 않는다.
 * 여기서는 제목을 그 낱말로 가르고 글로 옮기기만 한다(CLAUDE.md "웹앱은 계산하지 않는다").
 */

/** 표본 하한 — batch/services/disclosure_reaction.MIN_N 과 같아야 한다 */
export const REACTION_MIN_N = 30;

export interface ReactionRow {
  type: string;
  label: string;
  keywords: string;
  priority: number;
  n: number;
  mean_pct: number | null;
  median_pct: number | null;
  pos_pct: number | null;
  window_days: number;
}

export const REACTION_ROWS = `SELECT type, label, keywords, priority, n, mean_pct, median_pct, pos_pct, window_days
FROM disclosure_reaction ORDER BY priority`;

/** 제목 → 유형 줄. 배치 `classify` 와 같은 규칙: 공백을 빼고, "정정" 이면 없음, 우선순위대로 처음 맞는 것 */
export function classifyTitle(title: string, rows: ReactionRow[]): ReactionRow | null {
  const t = title.replace(/\s/g, "");
  if (t.includes("정정")) return null;
  for (const r of [...rows].sort((a, b) => a.priority - b.priority)) {
    let words: string[] = [];
    try {
      words = JSON.parse(r.keywords) as string[];
    } catch {
      continue;
    }
    if (words.some((w) => t.includes(w.replace(/\s/g, "")))) return r;
  }
  return null;
}

const signed = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;

export function reactionLine(r: ReactionRow | null): string | null {
  if (!r || r.n < REACTION_MIN_N || r.median_pct === null || r.mean_pct === null || r.pos_pct === null) return null;
  // 공시일 반응이 들어 있다 — 알림 뒤에 얻을 수 있는 몫이 아니다 (25.1013)
  return `${r.label} 공시: 전날 종가부터 ${r.window_days}거래일째까지(공시일 반응 포함) 지수 대비 중앙값 ${signed(r.median_pct)} · 평균 ${signed(r.mean_pct)} · 오른 비율 ${r.pos_pct.toFixed(0)}% (${r.n}건)`;
}
