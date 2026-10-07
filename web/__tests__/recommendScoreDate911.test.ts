import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// 추천 카드의 종합·순위·팩터·센티먼트에 기준일이 없었다 — 근거표에 종합 점수 행이 없어 어느 날 점수로 골랐는지 확인할 수 없었다
// (CLAUDE.md 절대 규칙: 근거에 쓴 수치는 언제 기준인지 펼쳐 볼 수 있어야 한다. docs/infra.md 25.911, 감사)
describe("추천 카드의 점수 기준일 (25.911)", () => {
  it("질의가 점수 기준일을 읽는다", () => {
    expect(readFileSync("lib/recommend.ts", "utf-8")).toContain("sc.as_of_date AS score_date");
  });
  it("카드가 기준일과 출처를 적는다", () => {
    const 글 = readFileSync("components/RecommendList.tsx", "utf-8");
    expect(글).toContain("row.score_date");
    expect(글).toMatch(/점수\(scores\)/);
  });
});
