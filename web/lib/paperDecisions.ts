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

type After = Pick<DecisionRow, "ret_5d" | "ret_20d" | "ret_60d" | "bench_5d" | "bench_20d" | "bench_60d">;

/**
 * 견줄 창 — 수익과 지수가 **둘 다 있는** 가장 긴 창, 없으면 수익만 있는 가장 긴 창 (25.1505, 검토 7).
 * 60일 수익이 차면 그 행은 성과 채우기에서 빠져 60일 지수가 영영 비는 수가 있다 — 그때 20일 지수와 견준다
 */
function 창(d: After): [string, number, number | null] | null {
  const 창들: Array<[string, number | null, number | null]> = [
    ["60일", d.ret_60d, d.bench_60d],
    ["20일", d.ret_20d, d.bench_20d],
    ["5일", d.ret_5d, d.bench_5d],
  ];
  const 둘 = 창들.find(([, r, b]) => r != null && b != null);
  const 하나 = 창들.find(([, r]) => r != null);
  const 고름 = 둘 ?? 하나;
  return 고름 ? [고름[0], 고름[1] as number, 고름[2]] : null;
}

/**
 * 그 뒤 성과 글 — "20일 +3.1% (지수 +1.2%)" 처럼 견줄 창 하나.
 * 아직 안 찼으면 "아직(결정 뒤 5 거래일 전)", 못 채우고 닫았으면 그 까닭
 */
export function afterText(d: After & Pick<DecisionRow, "outcome_note">): string {
  const c = 창(d);
  if (c) return `${c[0]} ${pct(c[1])}${c[2] != null ? ` (지수 ${pct(c[2])})` : ""}`;
  return d.outcome_note ? `성과 없음 — ${d.outcome_note}` : "아직(결정 뒤 5 거래일 전)";
}

/** 그 뒤가 지수보다 나았나 — 색칠만. 견줄 창에 지수가 없으면 null */
export function beatBench(d: After): boolean | null {
  const c = 창(d);
  return c && c[2] != null ? c[1] > c[2] : null;
}

/**
 * **들고 있는 쪽** 줄인가 (25.1505, 검토 3) — 매도 플래그를 따르지 않고 든 것·위험 점검이 매도를 막은 것·이미 들고 있어 더 사지 않은 것
 * (배치 `NONCOMPARABLE_SKIP_PREFIXES` 의 보유 둘). 이 줄은 오르면 들고 간 것이 옳았다 — "놓침" 색이 반대 뜻이 된다
 */
export const HOLD_SIDE_SKIP_PREFIXES = ["종목 비중 상한 도달", "보유 중 손절선 이탈"] as const;
export function holdSide(d: Pick<DecisionRow, "kind" | "reason">): boolean {
  if (d.kind === "hold_flag") return true;
  if (d.kind === "blocked") return d.reason.startsWith("위험 점검(매도)");
  return d.kind === "skip" && HOLD_SIDE_SKIP_PREFIXES.some((p) => d.reason.startsWith(p));
}

export interface DecisionGroup { first: DecisionRow; last: DecisionRow; count: number }

/**
 * 같은 (종목, 종류, 까닭) 줄을 묶는다 — 들고 있는 종목은 날마다 한 줄씩 쌓여 60줄이 며칠치 같은 종목으로 찼다(검토 3).
 * 최근 것 순. 그 뒤 성과는 묶음의 **첫 결정**(가장 이른 줄) 것을 보인다 — 최근 줄은 아직 안 찼다
 */
export function groupDecisions(rows: DecisionRow[]): DecisionGroup[] {
  const 묶음 = new Map<string, DecisionGroup>();
  for (const d of rows) {
    const 키 = `${d.ticker}|${d.kind}|${d.reason}`;
    const g = 묶음.get(키);
    if (!g) 묶음.set(키, { first: d, last: d, count: 1 });
    else {
      g.count += 1;
      if (d.decided_on < g.first.decided_on) g.first = d;
      if (d.decided_on > g.last.decided_on) g.last = d;
    }
  }
  return [...묶음.values()].sort((x, y) => y.last.decided_on.localeCompare(x.last.decided_on) || y.last.id - x.last.id);
}
