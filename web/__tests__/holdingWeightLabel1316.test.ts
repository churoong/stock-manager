// 포트폴리오 화면의 종목별 % 는 보유 대비라고 적는다 — 2부는 총액 대비 (docs/infra.md 25.1316, 2부 감사 #4)
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("보유 % 이름표", () => {
  it("보유 중 x% 로 적는다", () => {
    const src = readFileSync(new URL("../components/PortfolioView.tsx", import.meta.url), "utf8");
    expect(src).toContain('(보유 중 {p.weight_pct?.toFixed(1) ?? "-"}%)');
  });
});
