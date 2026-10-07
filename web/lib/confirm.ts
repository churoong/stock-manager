/**
 * 저장 전 "그래도 저장할까요?" — 매매·배당 폼의 확인 루프 **하나** (docs/infra.md 25.945).
 *
 * 2026-10-04 까지 같은 모양이 셋 따로 있었다 — 같은 매매 두 번(25.785), 체결가가 종가와 100배(25.789), 원금보다 큰 배당(25.934).
 * 서버는 저마다 다른 깃발(`duplicate`·`price_unit`·`size_unit`)을 409 로 주고, 화면은 저마다 `if` 를 하나씩 더해 `submit(true, confirmPrice)`
 * 처럼 위치 인자로 확인을 넘겼다. 넷째가 생기면 또 따로 만들게 된다. 이제 서버는 `confirm_needed: "duplicate" | "price_unit" | "size"`
 * 하나를 함께 주고(옛 깃발도 남긴다), 화면은 이 파일의 루프 하나로 묻는다 — 확인한 종류를 모아 다시 보낸다.
 *
 * **순서는 서버가 정한다.** 화면은 서버가 지금 묻는 것 하나에만 답한다. 중복이 체결가보다 먼저인 것(25.790)은 route 의 순서다.
 */

export type ConfirmKind = "duplicate" | "price_unit" | "size";

export const CONFIRM_KINDS: readonly ConfirmKind[] = ["duplicate", "price_unit", "size"];

/** 서버로 다시 보낼 때 다는 깃발 — route 의 zod 스키마 이름과 같다 */
export const CONFIRM_FLAG: Record<ConfirmKind, "confirm_duplicate" | "confirm_price" | "confirm_size"> = {
  duplicate: "confirm_duplicate",
  price_unit: "confirm_price",
  size: "confirm_size",
};

/** 옛 응답 깃발 — `confirm_needed` 가 없는 응답(배포 사이)도 읽는다 */
const LEGACY_KEY: Record<ConfirmKind, "duplicate" | "price_unit" | "size_unit"> = {
  duplicate: "duplicate",
  price_unit: "price_unit",
  size: "size_unit",
};

/** 확인 창의 둘째 문장 — "왜 그래도 저장할 수 있는지" */
export const CONFIRM_HINT: Record<ConfirmKind, string> = {
  duplicate: "같은 체결이 정말 한 번 더 있었으면 확인을 눌러 저장하세요.",
  price_unit: "큰 역분할 전의 매매라 이 값이 맞으면 확인을 눌러 저장하세요.",
  size: "기록 전부터 들고 있던 주식의 배당이라 이 금액이 맞으면 확인을 눌러 저장하세요.",
};

/** 취소했을 때의 짧은 말 */
export const CONFIRM_CANCEL: Record<ConfirmKind, string> = {
  duplicate: "같은 기록이 이미 저장됐습니다",
  price_unit: "체결가를 확인해 주세요",
  size: "배당 금액을 확인해 주세요",
};

/**
 * 서버가 **지금** 묻는 확인 종류. 이미 확인해 보낸 종류는 묻지 않는다(서버가 또 물으면 안 되지만, 물어도 루프가 돌지 않게).
 * 응답이 성공이거나 묻는 것이 없으면 null.
 */
export function confirmNeeded(body: Record<string, unknown>, confirmed: ReadonlySet<ConfirmKind>): ConfirmKind | null {
  const 명시 = body.confirm_needed;
  if (typeof 명시 === "string" && (CONFIRM_KINDS as readonly string[]).includes(명시) && !confirmed.has(명시 as ConfirmKind)) {
    return 명시 as ConfirmKind;
  }
  for (const kind of CONFIRM_KINDS) {
    if (body[LEGACY_KEY[kind]] === true && !confirmed.has(kind)) return kind;
  }
  return null;
}

/** 확인한 종류들을 요청 본문에 다는 깃발로. 확인한 것만 싣는다 — 안 한 것을 `false` 로 보내지 않는다 */
export function confirmFlags(confirmed: ReadonlySet<ConfirmKind>): Partial<Record<(typeof CONFIRM_FLAG)[ConfirmKind], true>> {
  const out: Partial<Record<(typeof CONFIRM_FLAG)[ConfirmKind], true>> = {};
  for (const kind of confirmed) out[CONFIRM_FLAG[kind]] = true;
  return out;
}

/** 확인 창 본문 — 서버의 사유 + 둘째 문장 */
export function confirmPrompt(kind: ConfirmKind, errors: string[] | undefined): string {
  return `${errors?.join(" ") ?? CONFIRM_CANCEL[kind]}\n\n${CONFIRM_HINT[kind]}`;
}
