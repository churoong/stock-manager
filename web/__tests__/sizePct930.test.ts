/** 백테스트 표의 변동성·이긴 달에 "+" 가 붙어 이익처럼 읽혔다 (docs/infra.md 25.930). */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { pct, sizePct } from "@/lib/portfolio";

describe("sizePct", () => {
  it("부호를 붙이지 않는다", () => {
    expect(sizePct(0.18)).toBe("18.0%");
    expect(sizePct(0.574, 0)).toBe("57%");
    expect(sizePct(null)).toBe("-");
    expect(pct(0.18)).toBe("+18.0%"); // 수익률은 그대로 부호
  });

  it("백테스트 화면의 변동성·이긴 달은 부호 없는 쪽을 쓴다", () => {
    const src = readFileSync(join(process.cwd(), "components", "BacktestView.tsx"), "utf-8");
    expect(src).not.toMatch(/[^e]pct\(metricNumber\(m, "volatility_ann"\)\)/);
    expect(src).not.toMatch(/[^e]pct\(r\.win_rate/);
    expect(src).toContain('sizePct(metricNumber(m, "volatility_ann"))');
  });
});
