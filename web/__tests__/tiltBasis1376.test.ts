import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { tiltBasisLabel } from "@/lib/etfTilt";

/** 집중 목록 머리는 행이 적은 점수 기준을 말한다 (docs/infra.md 25.1376) — 기준 칸이 없는 행은 옛 종합 점수 순위다 */
describe("집중 목록 점수 기준", () => {
  it("value_quality 면 밸류·퀄리티, 없으면 이전 기준", () => {
    expect(tiltBasisLabel({ score_basis: "value_quality" })).toBe("우리 장기 점수(밸류·퀄리티)");
    expect(tiltBasisLabel({})).toContain("종합 점수(이전 기준");
    expect(tiltBasisLabel(null)).toContain("이전 기준");
  });

  it("배치가 그 칸을 싣고 화면이 그것을 읽는다", () => {
    expect(readFileSync("../batch/jobs/etf_tilt.py", "utf8")).toContain('"score_basis": SCORE_BASIS');
    expect(readFileSync("components/EtfList.tsx", "utf8")).toContain("tiltBasisLabel(first)");
  });
});
