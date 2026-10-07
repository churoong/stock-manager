import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/** 복기 화면 비율도 성적표와 같은 `rateText` 로 — 199/200 이 "100%" 였다 (docs/infra.md 25.704) */
describe("복기 비율 반올림", () => {
  it("toFixed(0) 퍼센트를 직접 만들지 않는다", () => {
    const 원본 = readFileSync(join(__dirname, "..", "components", "PortfolioView.tsx"), "utf8");
    const 복기 = 원본.slice(원본.indexOf("function ReviewStats"));
    expect(복기).toContain("rateText(v)");
    expect(복기.slice(0, 복기.indexOf("\n}\n"))).not.toMatch(/_rate \* 100\)\.toFixed\(0\)/);
  });
});
