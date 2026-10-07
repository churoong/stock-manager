/**
 * 추천이 **왜 비어 있는지** 를 가린다 (docs/infra.md 25.86).
 *
 * 화면이 "표시할 추천이 없습니다" 만 말하면 사용자는 세 가지를 구별할 수 없다.
 *
 *   1. 배치가 아직 한 번도 안 돌았다
 *   2. 배치는 돌았는데 **재료가 없어** 낼 것이 없었다
 *   3. 재료는 다 있는데 **오늘 조건을 만족한 종목이 없다** (정상이다)
 *
 * 2026-09-21 까지 국내 쪽 문구는 이랬다.
 *
 * > 아직 신호를 계산한 적이 없습니다. **매수 신호 배치를 먼저 돌리세요**
 *
 * `signals` 표가 0행이면 무조건 이 문장이었다. 그런데 따라잡기 7단계가 신호 배치를
 * **매일 돌리고 있었다** — 돌았지만 재료(시세 거래일·재무·업종)가 없어 0건이었을 뿐이다.
 * 즉 화면이 **오진하고**, 사용자에게 해도 소용없는 일을 시켰다.
 *
 * 2026-09-19 D1 실측이 정확히 2번이었다: 시세 88거래일, 재무 0, 업종 0.
 */

/**
 * 종합 점수에 필요한 거래일. **파이썬이 단일 정의처다**
 * (`scripts/catchup_report.py` 의 `MOMENTUM_DAYS` · `RISK_DAYS`).
 * `tests/test_recommend_reason.py` 가 두 값이 같은지 본다 — 갈라지면 파이썬 테스트가 깨진다.
 */
export const MOMENTUM_DAYS = 126;
export const RISK_DAYS = 200;

export type 재료 = {
  /** `signals` 배치가 성공(또는 부분성공)한 횟수. 0 이면 **정말로** 돈 적이 없다 */
  signalRuns: number | null;
  /** 그 나라의 시세 거래일 수 */
  priceDays: number | null;
  /** 재무가 있는 종목 수 */
  financials: number | null;
  /** 업종이 붙은 종목 수 */
  sectors: number | null;
};

/**
 * 아직 모자란 재료의 이름. **`null`(못 읽음)은 빈 것으로 치지 않는다** — 못 읽은 것과
 * 없는 것은 다르다(docs/infra.md 25.74).
 */
export function missingInputs(재료: 재료): string[] {
  const 빈것: string[] = [];
  if (재료.priceDays !== null && 재료.priceDays < RISK_DAYS) {
    빈것.push(`시세 ${재료.priceDays.toLocaleString()}/${RISK_DAYS}거래일`);
  }
  if (재료.financials === 0) 빈것.push("재무 없음");
  if (재료.sectors === 0) 빈것.push("업종 없음");
  return 빈것;
}

/**
 * 신호가 0건일 때 화면에 적을 한 줄.
 *
 * **해도 소용없는 일을 시키지 않는다.** 배치가 이미 돌고 있다면 "배치를 돌리세요" 는
 * 거짓 안내다 — 기다리는 것이 답인 상황에서 사람을 헤매게 한다.
 */
export function whyNoSignals(재료: 재료): string {
  if (재료.signalRuns === 0) {
    return "아직 신호를 계산한 적이 없습니다. 매수 신호 배치를 먼저 돌리세요";
  }

  const 모자람 = missingInputs(재료);
  if (모자람.length > 0) {
    // **평문이다.** 화면은 이 글자를 `{note}` 로 그대로 찍는다(RecommendList) —
    // `**굵게**` 나 `[글](주소)` 를 넣으면 **별표와 괄호가 그대로 보인다.**
    // 2026-09-21 에 이 줄에 `**` 를 넣었다가 바로 걷어냈다(docs/infra.md 25.89).
    // 스크리너로 건너가는 **진짜 링크**는 화면 쪽(RecommendList)이 따로 단다.
    return (
      `신호 배치는 돌았지만 낼 것이 없었습니다 — 재료가 아직 모자랍니다 (${모자람.join(" · ")}).` +
      " 데이터를 채우는 중이며, 갖춰지면 저절로 나옵니다. 고장이 아닙니다"
    );
  }

  if (재료.signalRuns === null) {
    // 실행 이력을 못 읽었다. **모르면서 단정하지 않는다**
    return "표시할 추천이 없습니다. 배치 실행 기록을 읽지 못해 이유를 가리지 못했습니다";
  }

  return (
    "재료는 갖춰졌는데 오늘 조건을 만족한 종목이 없습니다." +
    " 조건을 만족하지 못한 것이지 오류가 아닙니다"
  );
}
