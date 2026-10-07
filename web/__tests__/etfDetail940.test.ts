/** ETF 상세의 보통주 전용 구역이 보통주의 까닭을 보였다 (docs/infra.md 25.940, 감사). */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ETF_SECTION_NOTE } from "@/lib/stockDetail";

describe("ETF 상세 빈 구역", () => {
  it("분배금이 없다고 말하지 않는다", () => {
    expect(ETF_SECTION_NOTE.dividends).toContain("모으지 않습니다");
    expect(ETF_SECTION_NOTE.dividends).not.toContain("무배당");
    expect(Object.values(ETF_SECTION_NOTE).join(" ")).not.toContain("아직");
  });

  it("다섯 구역 모두 ETF 일 때 이 글을 쓴다", () => {
    const src = readFileSync(join(process.cwd(), "components", "StockDetail.tsx"), "utf-8");
    for (const k of Object.keys(ETF_SECTION_NOTE)) {
      expect(src).toContain(`emptyNote={etf ? ETF_SECTION_NOTE.${k} : undefined}`);
    }
    expect(src).toContain('const etf = stock.asset_type === "etf";');
  });
});
