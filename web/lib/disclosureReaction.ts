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
  /** 5거래일째 거래정지로 못 잰 건수 (25.1154). 마이그레이션 0062 전 행에는 없다 */
  halted?: number | null;
}

// `*` 로 읽는다 — `halted` 열(0062)이 아직 없는 DB 에서도 장중 경로가 깨지지 않게 (25.1154)
export const REACTION_ROWS = `SELECT * FROM disclosure_reaction ORDER BY priority`;

/** 이 낱말이 있으면 어느 유형도 아니다 — batch/services/disclosure_reaction.EXCLUDE 와 같아야 한다 (25.1153) */
export const REACTION_EXCLUDE = ["정정", "해지", "결과보고", "종속회사", "자회사의주요경영사항"];

/** 제목 → 유형 줄. 배치 `classify` 와 같은 규칙: 공백을 빼고, 빼는 낱말이 있으면 없음, 우선순위대로 처음 맞는 것 */
export function classifyTitle(title: string, rows: ReactionRow[]): ReactionRow | null {
  const t = title.replace(/\s/g, "");
  if (REACTION_EXCLUDE.some((w) => t.includes(w))) return null;
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
  // 정지로 못 잰 건수를 말한다 (25.1154) — 배치 `line` 과 같은 문장
  const 정지 = Number(r.halted ?? 0);
  return `${r.label} 공시: 전날 종가부터 ${r.window_days}거래일째까지(공시일 반응 포함) 지수 대비 중앙값 ${signed(r.median_pct)} · 평균 ${signed(r.mean_pct)} · 오른 비율 ${r.pos_pct.toFixed(0)}% (${r.n}건${정지 ? `, 거래정지로 못 잰 ${정지}건 빠짐` : ""})`;
}

/**
 * 장중 공시 알림의 **대표 제목** (docs/infra.md 25.1396, 공시 감사 ③). 예전에는 접수번호가 가장 큰 것을 골랐는데
 * 거래소 경유(`80…`)가 금감원 직접 제출(`00…`)보다 늘 커서(25.1198), 같은 날 "주요사항보고서(유상증자결정)" 과
 * "기업설명회(IR)개최" 가 함께 잡히면 IR 이 대표가 되고 유상증자의 반응 줄은 붙지 않았다 — 하루 한 번 규칙이라 그날 다시
 * 알리지도 않는다. 이제 반응 유형에 걸리는 것 가운데 **우선순위가 가장 높은 것**(같으면 앞 — 접수번호 큰 쪽), 하나도 없으면
 * 첫 것(접수번호 최댓값, 예전과 같다)
 */
export function representativeDisclosure<T extends { title: string }>(
  items: T[],
  rows: ReactionRow[],
): { item: T; row: ReactionRow | null } | null {
  let best: { item: T; row: ReactionRow | null } | null = null;
  for (const item of items) {
    const row = classifyTitle(item.title, rows);
    if (!best) best = { item, row };
    if (row && (!best.row || row.priority < best.row.priority)) best = { item, row };
  }
  return best;
}
