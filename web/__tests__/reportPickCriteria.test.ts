import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { pickCriteria } from "@/lib/reports";

describe("리포트 1부 추천의 근거표 (docs/infra.md 25.349)", () => {
  it("실린 근거를 표 재료로 넘긴다", () => {
    expect(pickCriteria({ criteria: [{ label: "매출" }] })).toBe(
      '{"criteria":[{"label":"매출"}]}',
    );
  });

  it("0줄은 빈 표(빨간 경고)로, 싣기 전 리포트는 null 로 구별한다", () => {
    expect(pickCriteria({ criteria: [] })).toBe('{"criteria":[]}');
    expect(pickCriteria({})).toBeNull();
  });

  it("리포트 화면이 1부 추천마다 근거표를 그린다", () => {
    const src = readFileSync("components/ReportView.tsx", "utf8");
    expect(src).toMatch(
      /section === "recommend" && \(\s*<span className="basis-full">\s*\{pickCriteria\(item\.payload\)/,
    );
    expect(src).toContain("<CriteriaTable");
  });
});
