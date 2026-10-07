import { z } from "zod";

/**
 * 입력 검증 오류를 **사람이 읽는 한국어 한 줄**로 (docs/infra.md 25.264).
 *
 * 경로들이 `issues.map((i) => i.message)` 만 돌려줘서, 매매 저장에서 가격·수량을 비우면 화면에
 * "Invalid input: expected number, received null, Invalid input: …" 가 **영어로, 어느 칸인지 없이** 떴다.
 * zod 한국어 로케일을 켜고 칸 이름을 앞에 붙인다. 스키마에 적어 둔 한국어 문구("체결가는 0 보다 커야 합니다")는 그대로 쓴다.
 */
z.config(z.locales.ko());

/** 요청 칸 이름 → 화면 이름. 없는 칸은 칸 이름 그대로 */
export const FIELD_LABEL: Record<string, string> = {
  stock_id: "종목",
  side: "매수·매도",
  trade_date: "체결일",
  pay_date: "지급일",
  price: "체결가",
  quantity: "수량",
  fx_rate: "환율",
  fee: "수수료",
  tax: "세금",
  horizon: "투자 기간",
  memo: "메모",
  amount_per_share: "주당 배당금",
  gross_amount: "세전 배당금",
  target_buy_price: "목표 매수가",
  alert_enabled: "알림",
  market: "시장",
  country: "나라",
  date: "날짜",
  name: "이름",
};

export function issueTexts(
  issues: ReadonlyArray<{ path: PropertyKey[]; message: string }>,
): string[] {
  return issues.map((i) => {
    const 칸 = i.path.map(String).join(".");
    if (!칸) return i.message;
    const 이름 = FIELD_LABEL[String(i.path[0])] ?? 칸;
    return `${이름}: ${i.message}`;
  });
}
