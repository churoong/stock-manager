import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { pnlClass } from "@/lib/portfolio";

/** 손익 색이 반올림 전 값을 따라 "0원" 이 빨강·파랑이던 세 곳 (docs/infra.md 25.737, 감사) */
describe("손익 색은 보이는 숫자를 따른다", () => {
  it("반올림해 0원이면 색이 없다", () => {
    expect(pnlClass(-0.3)).toBe("");
    expect(pnlClass(0.4)).toBe("");
    expect(pnlClass(1)).toBe("text-rose-600");
  });
  it("세 곳 모두 pnlClass 를 쓴다", () => {
    const detail = readFileSync("components/StockDetail.tsx", "utf8");
    const view = readFileSync("components/PortfolioView.tsx", "utf8");
    expect(detail).toContain("pnlClass(pnl)");
    expect(detail).not.toContain("pnl >= 0 ?");
    expect(view).toContain("const color = pnlClass(tone);");
    expect(view).toContain("pnlClass(l.realized_pnl_krw)");
  });
});

/** 퍼센트 값도 보이는 숫자로 칠한다 — 복기 행·백테스트 창 (docs/infra.md 25.741, 25.735~738 교차검증) */
describe("pctClass", () => {
  it("pct 가 0.0% 로 보이면 색이 없다", async () => {
    const { pctClass } = await import("@/lib/portfolio");
    expect(pctClass(0.0004)).toBe("");
    expect(pctClass(-0.0001)).toBe("");
    expect(pctClass(0)).toBe("");
    expect(pctClass(0.01)).toBe("text-rose-600");
    expect(pctClass(-0.01)).toBe("text-blue-600");
  });
  it("세 곳이 pctClass 를 쓴다", () => {
    const view = readFileSync("components/PortfolioView.tsx", "utf8");
    const bt = readFileSync("components/BacktestView.tsx", "utf8");
    expect(view).toContain("pctClass(r.return_pct)");
    expect((bt.match(/pctClass\(w\.return_pct\)/g) ?? []).length).toBe(2);
    expect(bt).not.toContain('w.return_pct < 0 ? "text-blue-600"');
  });
});
