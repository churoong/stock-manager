/**
 * 가상모의투자 계좌 화면 이름 (docs/paper.md 1장, docs/infra.md 25.1889 — 2026-10-28 사용자 지시 "각 가상계좌는 알파벳이 아닌
 * 계좌특성을 이름으로 바꿔"). 내부 식별자(A·B·…)는 그대로 두고 보이는 글만 바꾼다. 정의처는
 * `batch/services/paper.py` 의 `ACCOUNT_NAMES` — 이 사본은 시험이 대조한다.
 */
export const ACCOUNT_NAMES: Readonly<Record<string, string>> = {
  A: "가치",
  B: "기준",
  C: "모멘텀",
  D: "장기보유",
  E: "안정",
  G: "추세익절",
  H: "지주사",
  R1: "무작위1",
  R2: "무작위2",
  S: "스윙",
  T: "단타",
};

/** 계좌 화면 이름 — 모르는 계좌는 식별자 그대로 */
export function accountName(id: string | null | undefined): string {
  if (!id) return "";
  return ACCOUNT_NAMES[id] ?? id;
}
