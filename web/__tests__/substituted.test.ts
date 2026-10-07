import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  METRIC_LABELS,
  SUBSTITUTED_KEY,
  priceRiskNote,
  substitutedNote,
} from "@/lib/stockDetail";

/**
 * **치환한 사실을 보여 준다** (docs/factors.md 3.5, docs/infra.md 25.93).
 *
 * `mdd_recovery_days` 가 없는 종목(=아직 회복하지 못한 종목)은 그 집단의 **최악값**으로
 * 대신 점수를 받는다. 그런데 `raw_json` 에는 null 이 들어가고 `missing_fields` 에도
 * 안 들어간다. 그래서 화면은 이렇게 보였다.
 *
 * > 안정성 41 · market:KOSPI (900종목)
 *
 * 값이 비었는데 점수는 있다. **"이 숫자가 어디서 왔나" 에 답할 수 없다** —
 * CLAUDE.md 의 "근거에 쓴 모든 수치는 어느 행에서 왔는지 확인할 수 있어야 한다" 가
 * 그 자리에서 깨진다. 문서는 2026-09-16 부터 raw_json 에 적으라고 요구하고 있었다.
 */

const 뿌리 = join(process.cwd(), "..");

describe("치환 근거", () => {
  it("치환이 있으면 한 줄로 적는다", () => {
    const 글 = substitutedNote({ [SUBSTITUTED_KEY]: { mdd_recovery_days: 1234 } });

    expect(글).toContain("MDD 회복 기간");
    expect(글).toContain("1,234");
    expect(글).toContain("미회복이라");
    expect(글).not.toContain("집단 최악"); // 25.693 — 채운 값은 집단 최악이 아닐 수 있다
  });

  it("치환이 없으면 빈 문자열", () => {
    expect(substitutedNote({ mdd_abs: 0.3 })).toBe("");
    expect(substitutedNote({})).toBe("");
  });

  it("모양이 이상하면 빈 문자열 — 화면을 깨뜨리지 않는다", () => {
    expect(substitutedNote({ [SUBSTITUTED_KEY]: "뭔가" })).toBe("");
    expect(substitutedNote({ [SUBSTITUTED_KEY]: null })).toBe("");
    expect(substitutedNote({ [SUBSTITUTED_KEY]: { mdd_recovery_days: null } })).toBe("");
  });

  it("평문이다 — 화면이 그대로 찍는다", () => {
    const 글 = substitutedNote({ [SUBSTITUTED_KEY]: { mdd_recovery_days: 1234 } });

    expect(글).not.toContain("**");
    expect(글).not.toContain("](");
  });

  it("예약 키가 파이썬과 같은 글자다", () => {
    // 갈라지면 화면은 **영원히 아무것도 안 보여 준다.** 오류도 안 난다
    const py = readFileSync(join(뿌리, "batch", "services", "scoring.py"), "utf8");
    const 적힌것 = /SUBSTITUTED_KEY\s*=\s*"([^"]+)"/.exec(py);

    expect(적힌것, "파이썬 쪽 SUBSTITUTED_KEY 를 못 읽었다").not.toBeNull();
    expect(적힌것![1]).toBe(SUBSTITUTED_KEY);
  });

  it("지표 이름이 아니라 예약어임을 알 수 있다", () => {
    // 밑줄로 시작하지 않으면 지표 이름과 섞여 근거표에 "_substituted" 라는 지표가 생긴다
    expect(SUBSTITUTED_KEY.startsWith("_")).toBe(true);
    expect(Object.keys(METRIC_LABELS)).not.toContain(SUBSTITUTED_KEY);
  });
});

describe("성과지표 표에 없는 리스크 지표", () => {
  it("셋을 다 적는다", () => {
    const 글 = priceRiskNote({
      amihud_illiquidity: 0.1234,
      max_daily_return: 0.0812,
      idio_volatility: 0.3456,
    });

    expect(글).toContain("Amihud 0.123 (거래대금 10억당 |일 수익률|)");
    expect(글).toContain("8.1%");
    expect(글).toContain("34.6%");
  });

  it("Amihud 는 유효숫자 셋과 통화 단위로 — 대형주가 0.000 으로 뭉개지지 않는다 (25.941)", () => {
    expect(priceRiskNote({ amihud_illiquidity: 0.0000152 }, "KRW")).toBe("Amihud 0.0000152 (10억원당 |일 수익률|)");
    expect(priceRiskNote({ amihud_illiquidity: 0.00213 }, "USD")).toBe("Amihud 0.00213 (10억달러당 |일 수익률|)");
    expect(priceRiskNote({ amihud_illiquidity: 0.00213 }, "USD")).not.toBe(priceRiskNote({ amihud_illiquidity: 0.0000152 }, "USD"));
  });

  it("있는 것만 적는다", () => {
    expect(priceRiskNote({ idio_volatility: 0.3 })).toBe("잔차 변동성 30.0%");
    expect(priceRiskNote({})).toBe("");
  });

  it("비율은 퍼센트로 적는다 — 단위를 틀리게 말하지 않는다", () => {
    // 0.0812 를 "0.08" 로 적으면 사용자는 8% 를 0.08% 로 읽는다
    expect(priceRiskNote({ max_daily_return: 0.0812 })).not.toContain("0.08 ");
  });
});

describe("지표 이름이 단위를 말한다", () => {
  it("MAX 는 '률' 이지 '일' 이 아니다", () => {
    // 값은 수익률이다. "최대 상승일" 은 날짜로 읽힌다 (2026-09-21 에 고쳤다)
    expect(METRIC_LABELS.max_daily_return).toContain("률");
    expect(METRIC_LABELS.max_daily_return).not.toMatch(/상승일$/);
  });

  it("잔차 변동성은 연율임을 밝힌다", () => {
    // √252 로 연환산한 값이다. 안 적으면 일간 변동성으로 읽힌다
    expect(METRIC_LABELS.idio_volatility).toContain("연율");
  });

  it("배당수익률에 이름이 있다", () => {
    expect(METRIC_LABELS.dividend_yield).toBeTruthy();
  });
});

describe("화면이 실제로 부르나", () => {
  /** **계산만 있고 안 불리면 없는 것과 같다** (docs/infra.md 25.65·25.92 가 그랬다). */
  const 본문 = readFileSync(join(뿌리, "web", "components", "StockDetail.tsx"), "utf8");

  it("치환 근거를 부른다", () => {
    expect(본문).toContain("substitutedNote(");
  });

  it("리스크 원지표를 부른다", () => {
    expect(본문).toContain("priceRiskNote(");
  });

  it("밸류 원지표에 배당수익률이 있다", () => {
    // 밸류는 2026-09-21 에 넷이 됐다. 빠뜨리면 점수에는 들어갔는데 근거표에 없는 지표가 생긴다
    expect(본문).toContain("value.raw.dividend_yield");
  });
});
