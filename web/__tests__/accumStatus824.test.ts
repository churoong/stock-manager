/** 적립 후보는 지금 상태로 거른다 (docs/infra.md 25.824) — 판정은 월 1회라 폐지 뒤 한 달 동안 보였다 */
import { describe, expect, it } from "vitest";
import { buildAccumulationPassedQuery } from "@/lib/accumulation";

describe("적립 후보 상태 조건", () => {
  it("제외·폐지된 종목을 빼고, 자리표시자와 인자 수가 맞다", () => {
    const q = buildAccumulationPassedQuery("KR");
    expect(q.sql).toContain("p.passed = 1 AND s.status = 'active'");
    // 25.858 에서 판정 버전 하위 질의가 나라 인자 둘을 더 받는다
    expect(q.args).toEqual(["KR", "KR", "KR", "KR"]);
    expect((q.sql.match(/\?/g) ?? []).length).toBe(q.args.length);
  });
});
