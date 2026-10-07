/** 장중 감시가 버리는 목표 매수가를 저장·목록에서 말한다 (docs/infra.md 25.813, 관심·설정 감사) */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { WATCH_PRICE_MAX_RATIO as 장중배수, watchPriceUsable } from "@/lib/intraday";
import { WATCH_PRICE_MAX_RATIO, targetAboveCloseWarning, watchPriceIgnored } from "@/lib/watch";

describe("장중 감시가 쓰지 않는 목표 매수가", () => {
  it("저장 경고가 '곧바로 알림' 대신 '알림이 나가지 않습니다' 라고 말한다", () => {
    const 글 = targetAboveCloseWarning(16_000, 10_000);
    expect(글).toContain("알림이 나가지 않습니다");
    expect(글).not.toContain("곧바로");
    expect(targetAboveCloseWarning(12_000, 10_000)).toContain("곧바로"); // 1.5배 안은 예전 경고
  });

  it("저장 경고·목록·장중 판정이 한 배수를 쓴다", () => {
    expect(장중배수).toBe(WATCH_PRICE_MAX_RATIO);
    // 정의처는 lib/watch 하나 — intraday 는 다시 내보내기만 (25.817, 교차검증)
    const 장중 = readFileSync("lib/intraday.ts", "utf-8");
    expect(장중).toContain("export { WATCH_PRICE_MAX_RATIO };");
    expect(장중).not.toMatch(/const WATCH_PRICE_MAX_RATIO\s*=/);
    for (const 목표 of [14_000, 15_000, 15_001, 16_000]) {
      expect(watchPriceIgnored(목표, 10_000)).toBe(watchPriceUsable(목표, 10_000) === null);
    }
    expect(watchPriceIgnored(null, 10_000)).toBe(false);
    expect(watchPriceIgnored(16_000, null)).toBe(false);
  });

  it("목록에 '장중 감시 제외' 를 띄운다", () => {
    expect(readFileSync("components/AlertCenter.tsx", "utf-8")).toContain("watchPriceIgnored(w.target_buy_price, w.last_close)");
  });
});
