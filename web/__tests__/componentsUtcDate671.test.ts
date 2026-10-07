/** 화면 컴포넌트는 "오늘" 을 UTC 날짜로 자르지 않는다 (docs/infra.md 25.671, 감사 — 25.207·25.236 과 같은 모양). */

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("컴포넌트의 오늘", () => {
  it("toISOString().slice(0, 10) 을 쓰지 않는다 — localDate/userDateOf 를 쓴다", () => {
    const 뿌리 = join(process.cwd(), "components");
    const 걸린 = readdirSync(뿌리)
      .filter((f) => f.endsWith(".tsx") || f.endsWith(".ts"))
      .filter((f) => readFileSync(join(뿌리, f), "utf8").includes("toISOString().slice(0, 10)"));
    expect(걸린).toEqual([]);
  });

  it("종목 상세 감성 칸은 그 종목 시장의 날짜로 잰다 (25.675)", () => {
    expect(readFileSync(join(process.cwd(), "components", "StockDetail.tsx"), "utf8")).toContain("localDate(market, new Date())");
  });
});
