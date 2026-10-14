import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/** 근거표 무위험수익률 줄이 "오늘 값을 과거 전체에 쓴다" 는 한계를 말한다 (docs/metrics.md, docs/infra.md 25.1362, 23회차 P6) */
describe("무위험수익률 줄", () => {
  it("값이 있을 때 과거 창 전체에 적용했다고 적는다", () => {
    const 원문 = readFileSync(join(process.cwd(), "components", "StockDetail.tsx"), "utf-8");
    expect(원문).toMatch(/risk_free_rate_used !== undefined \? "\(오늘 설정값을 과거 창 전체에 적용\)"/);
  });
});
