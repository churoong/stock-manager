/** 종목 화면 아래쪽 카드는 화면 가까이 왔을 때 읽는다 (docs/infra.md 25.1032 — 웹 DB 읽기 줄이기) */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const src = readFileSync(join(process.cwd(), "components", "StockDetail.tsx"), "utf-8");
const 아래 = ["valuation", "metrics", "financials", "quarterly", "dividends", "news", "events", "opinions"];
const 위 = ["prices", "signals", "verdict"];

describe("종목 화면 지연 읽기", () => {
  it("아래쪽 여덟 카드는 본 뒤에만 읽고, 카드에 같은 이름을 단다", () => {
    for (const k of 아래) {
      expect(src, k).toMatch(new RegExp(`useJson<SectionResult>\\(\`\\$\\{base\\}/${k}\`, !!seen\\.${k}\\)`));
      expect(src, k).toContain(`<Card lazy="${k}"`);
    }
  });

  it("위쪽(개요·분석·가격·신호)은 바로 읽는다", () => {
    for (const k of 위) expect(src, k).not.toMatch(new RegExp(`/${k}[^\\n]*!!seen`));
    expect(src).toContain("const overview = useJson<Overview>(base);");
  });

  it("관찰기가 없는 브라우저면 한꺼번에 읽는다", () => {
    expect(src).toContain('typeof IntersectionObserver === "undefined"');
  });

  it("폴링은 화면이 뒤에 있으면 읽지 않는다", () => {
    for (const f of ["PortfolioView.tsx", "BacktestView.tsx"]) {
      const s = readFileSync(join(process.cwd(), "components", f), "utf-8");
      expect(s, f).toContain('document.visibilityState !== "hidden"');
    }
  });
});
