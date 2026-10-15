import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { POSITION, positionFxNote } from "@/lib/stockDetail";

// 종목 상세의 외화 보유 원화 손익에 환율 날짜를 함께 (docs/infra.md 25.1435, 감사)
describe("보유 환율 기준", () => {
  it("환율 날짜와 값을 읽고, 7일 넘게 묵으면 알린다", () => {
    expect(POSITION).toContain("fx_date");
    expect(positionFxNote("USD", 1385.5, "2026-10-14", "2026-10-14")).toEqual({
      text: "환율 1,385.5원 (2026-10-14)",
      stale: false,
    });
    expect(positionFxNote("USD", 1385, "2026-10-01", "2026-10-14")?.stale).toBe(
      true,
    );
    expect(positionFxNote("USD", 1385, "2026-10-07", "2026-10-14")?.stale).toBe(
      false,
    );
    expect(positionFxNote("KRW", 1, "2026-10-14", "2026-10-14")).toBeNull();
    expect(positionFxNote("USD", null, null, "2026-10-14")).toBeNull();
  });

  it("상세 카드가 그 줄을 그린다", () => {
    expect(readFileSync("components/StockDetail.tsx", "utf8")).toContain(
      "positionFxNote(currency, position.fx_now, position.fx_date, position.price_date)",
    );
  });
});
