import { describe, expect, it } from "vitest";
import { FRESHNESS } from "@/lib/health";

/**
 * 신선도 칸 가운데 **시각(타임스탬프) 열**을 읽는 것은 모두 한국 날짜로 바꾼다 (25.240, docs/infra.md 25.374).
 * "신호 성적표" 두 칸만 빠져 UTC ISO 원문이 그대로 보였고, 월요일 21:07 UTC(화요일 06:07 KST)에 돈 것이 화요일에 "1일 전" 이었다.
 */
describe("신선도의 시각 열은 한국 날짜", () => {
  const 시각열 =
    /MAX\([\w.]*(fetched_at|computed_at|finished_at|built_at|updated_at|created_at)\)/;

  it("시각 열을 읽는 칸은 모두 '+9 hours' 로 날짜를 낸다", () => {
    const 빠진 = FRESHNESS.filter(
      (f) => 시각열.test(f.sql) && !f.sql.includes("'+9 hours'"),
    ).map((f) => f.key);
    expect(빠진).toEqual([]);
  });
});
