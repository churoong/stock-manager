/**
 * 책임 고지 — CLAUDE.md 의 **절대 규칙**이다 (docs/infra.md 25.115).
 *
 * > 모든 화면 하단과 텔레그램 리포트 끝에 "투자 판단의 책임은 본인에게 있습니다" 고지
 *
 * **왜 상수가 따로 있나.** 파이썬은 `batch/config.DISCLAIMER` 하나를 `notify/telegram.send`
 * 가 **보내기 직전에 한 번** 붙인다. 웹은 그러지 않아서 같은 문장이 다섯 벌 손으로 적혀
 * 있었고, 그중 **무응답 알림 하나는 아예 안 붙이고 있었다.** 부르는 쪽마다 지키게 하면
 * 언젠가 빠뜨린다 — 실제로 빠뜨렸다.
 *
 * 글자는 파이썬 쪽과 같아야 한다. `tests/test_disclaimer.py` 가 두 언어를 대조한다.
 */
export const DISCLAIMER = "투자 판단의 책임은 본인에게 있습니다.";

/** 이미 들어 있으면 두 번 붙이지 않는다. 파이썬 `telegram.send` 와 같은 규칙 */
/** 텔레그램 끝에 고지를 붙이나 — 붙이지 않는다 (2026-10-02 사용자 지시, docs/infra.md 25.878). 화면 푸터는 그대로 */
export const TELEGRAM_DISCLAIMER = false;

export function withDisclaimer(text: string): string {
  if (!TELEGRAM_DISCLAIMER) return text;
  // **끝에 있는가** 를 본다 (docs/infra.md 25.613, 감사) — 본문 중간에 같은 문장이 있어도 끝에 붙인다
  return text.trimEnd().endsWith(DISCLAIMER) ? text : `${text}\n\n${DISCLAIMER}`;
}
