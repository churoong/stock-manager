import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * 성과 지표 꼬리말의 기준 지수 (docs/infra.md 25.686, 교차검증).
 *
 * 25.684 부터 기준 지수는 **베타를 낸 기간에만** 적힌다. 꼬리말이 첫 행(1Y)만 읽어, 1Y 베타는 못 내고 3Y 베타는 낸
 * 종목이 "베타 - 대비" 로 나왔다 — 3Y 베타 값은 표에 있는데 무엇 대비인지가 사라졌다.
 */
describe("기준 지수는 적힌 행에서 읽는다", () => {
  it("첫 행이 아니라 benchmark 가 있는 행", () => {
    const 원본 = readFileSync(join(__dirname, "..", "components", "StockDetail.tsx"), "utf8");
    expect(원본).toContain("rows.find((r) => r.benchmark)?.benchmark");
    expect(원본).not.toContain("rows[0]?.benchmark");
  });
});
