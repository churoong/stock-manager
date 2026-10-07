/** "표 없음" 일 때만 옛 질의로 — 다른 오류는 올린다 (docs/infra.md 25.667, 감사). */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ifMissingTableThen } from "@/lib/db";

describe("ifMissingTableThen", () => {
  it("표가 없으면 대신 묻는다", async () => {
    await expect(ifMissingTableThen(async () => "옛")(new Error("SQLITE_ERROR: no such table: signal_judgements"))).resolves.toBe("옛");
  });

  it("한도·시간 초과는 올린다 — 기준일을 바꿔 어제 신호를 오늘 것처럼 보이지 않게", async () => {
    await expect(ifMissingTableThen(async () => "옛")(new Error("D1_ERROR: timeout"))).rejects.toThrow("timeout");
  });

  it("두 경로가 모든 실패를 삼키는 `.catch(() =>` 로 옛 질의를 부르지 않는다", () => {
    for (const f of ["lib/stockDetail.ts", "app/api/recommend/route.ts"]) {
      expect(readFileSync(f, "utf8")).not.toMatch(/\.catch\(\(\) =>\s*\n?\s*(exec|execute)\((LAST_CALC_DATE|PREVIOUS_AS_OF)_FALLBACK/);
    }
  });
});
