/**
 * 왜 안 샀나 — 결정 원장에서 계좌 하나의 "안 한 결정" 을 읽는다 (docs/desk.md 3장, docs/paper.md 9장, docs/infra.md 25.1502).
 *
 * 안 낸 후보(skip)·위험 점검이 막은 것(blocked)·안 찬 회차(missed)·매도 플래그를 따르지 않고 든 것(hold_flag)과 **그 뒤 성과**.
 * 성과(그 뒤 5·20·60 거래일 수익과 같은 기간 지수)는 배치가 채운 값 그대로 — 웹은 빼지 않고 둘을 나란히 적는다.
 * 계좌·시장으로 고정해 UNIQUE 색인(account_id, market, decided_on, …)을 거꾸로 훑는다 — 최근 것부터 `DECISION_LIMIT` 줄 남짓만 읽는다
 * (산 것·판 것 줄은 건너뛰며 읽힌다).
 */

export const DECISION_LIMIT = 60;

export const DECISIONS_SQL = `SELECT d.id, d.decided_on, d.kind, d.reason, d.ret_5d, d.ret_20d, d.ret_60d, d.bench_5d, d.bench_20d, d.bench_60d,
  d.outcome_note, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name
FROM paper_decisions d JOIN stocks s ON s.id = d.stock_id
WHERE d.account_id = ? AND d.market = ? AND d.kind IN ('skip', 'blocked', 'missed', 'hold_flag')
ORDER BY d.decided_on DESC, d.id DESC LIMIT ${DECISION_LIMIT}`;

export interface DecisionRow {
  id: number; decided_on: string; kind: string; reason: string; ticker: string; name: string;
  ret_5d: number | null; ret_20d: number | null; ret_60d: number | null;
  bench_5d: number | null; bench_20d: number | null; bench_60d: number | null; outcome_note: string | null;
}

export const KIND_TEXT: Record<string, string> = {
  skip: "안 낸 후보",
  blocked: "위험 점검이 막음",
  missed: "안 찬 회차",
  hold_flag: "매도 플래그를 따르지 않고 듦",
};

/** 계좌 id 는 대문자로 시작하는 1~4자(A·B·R1 …) — 그 밖은 받지 않는다 */
export function validAccount(v: string | null): v is string {
  return v != null && /^[A-Z][A-Z0-9]{0,3}$/.test(v);
}

/** 수익·지수는 배치가 % 단위로 적었다(`batch/services/paper.outcomes` — 3.1 = +3.1%) */
const pct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;

/**
 * 그 뒤 성과 글 — "20일 +3.1% (지수 +1.2%)" 처럼 가장 긴 찬 창 하나.
 * 아직 안 찼으면 "아직(결정 뒤 5 거래일 전)", 못 채우고 닫았으면 그 까닭
 */
export function afterText(d: Pick<DecisionRow, "ret_5d" | "ret_20d" | "ret_60d" | "bench_5d" | "bench_20d" | "bench_60d" | "outcome_note">): string {
  const 창: Array<[string, number | null, number | null]> = [
    ["60일", d.ret_60d, d.bench_60d],
    ["20일", d.ret_20d, d.bench_20d],
    ["5일", d.ret_5d, d.bench_5d],
  ];
  const 찬 = 창.find(([, r]) => r != null);
  if (찬) {
    const [이름, r, b] = 찬;
    return `${이름} ${pct(r as number)}${b != null ? ` (지수 ${pct(b)})` : ""}`;
  }
  return d.outcome_note ? `성과 없음 — ${d.outcome_note}` : "아직(결정 뒤 5 거래일 전)";
}

/** 그 뒤가 지수보다 나았나 — 색칠만(안 산 게 올랐으면 놓친 것). 둘 중 하나라도 없으면 null */
export function beatBench(d: Pick<DecisionRow, "ret_5d" | "ret_20d" | "ret_60d" | "bench_5d" | "bench_20d" | "bench_60d">): boolean | null {
  for (const [r, b] of [[d.ret_60d, d.bench_60d], [d.ret_20d, d.bench_20d], [d.ret_5d, d.bench_5d]] as const) {
    if (r != null) return b == null ? null : r > b;
  }
  return null;
}
