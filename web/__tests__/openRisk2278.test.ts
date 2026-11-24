import { describe, expect, it } from "vitest";
import { openRiskText } from "@/lib/desk";

/** 위험판 "열린 위험" 칸 (docs/desk.md 6장, docs/infra.md 25.2278) */
describe("openRiskText", () => {
  it("잃는 몫이라 마이너스로, 소수 한 자리", () => {
    expect(openRiskText({ open_risk_pct: 4.249 })).toBe("−4.2%");
    expect(openRiskText({ open_risk_pct: 12.5 })).toBe("−12.5%");
  });
  it("0 은 마이너스 없이", () => {
    expect(openRiskText({ open_risk_pct: 0 })).toBe("0.0%");
    expect(openRiskText({ open_risk_pct: 0.04 })).toBe("0.0%"); // 25.2282
    expect(openRiskText({ open_risk_pct: 0.05 })).toBe("−0.1%");
  });
  it("값이 없으면 - (손절선으로 재지 않는 계좌 · 이 칸이 없던 날의 요약)", () => {
    expect(openRiskText({ open_risk_pct: null })).toBe("-");
    expect(openRiskText({})).toBe("-");
    expect(openRiskText({ open_risk_pct: Number.NaN })).toBe("-");
  });
  it("못 잰 보유 수를 숨기지 않는다", () => {
    expect(openRiskText({ open_risk_pct: 0.7, open_risk_skipped: 4 })).toBe(
      "−0.7% (4종목 못 잼)",
    );
    expect(openRiskText({ open_risk_pct: null, open_risk_skipped: 1 })).toBe(
      "- (1종목 못 잼)",
    );
    expect(openRiskText({ open_risk_pct: 3, open_risk_skipped: 0 })).toBe(
      "−3.0%",
    );
  });
});
