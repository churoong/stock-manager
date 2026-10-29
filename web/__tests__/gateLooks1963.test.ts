/**
 * 관문 판정 시점 한 줄 (25.1963) — 배치 `batch/services/desk.gate_looks_line` 과 같은 글을 낸다.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { gateLooksText } from "@/lib/desk";

describe("관문 판정 시점 (25.1963)", () => {
  it("판정 전·못 넘음·통과·끝·옛 관문", () => {
    expect(gateLooksText({ looks: [], next_look: 126, passed: false })).toBe("판정 시점(126·189·252평가에만 판정): 다음 126평가");
    expect(gateLooksText({ looks: [{ n: 126, at: 130, date: "2027-04-01", passed: false, failed: ["kis"] }], next_look: 189, passed: false }))
      .toBe("판정 시점(126·189·252평가에만 판정): 126 못 넘음 · 다음 189평가");
    const 끝 = [126, 189, 252].map((n) => ({ n, at: n, date: null, passed: false, failed: [] }));
    expect(gateLooksText({ looks: 끝, next_look: null, passed: false })).toBe(
      "판정 시점(126·189·252평가에만 판정): 126 못 넘음 · 189 못 넘음 · 252 못 넘음 · 판정 시점 끝 — 새 판 등록 전엔 통과 없음");
    expect(gateLooksText({ looks: [{ ...끝[0], passed: true }], next_look: 189, passed: true })).toContain("126 통과 · 다음 189평가");
    expect(gateLooksText({ passed: false })).toBeNull();
  });

  it("배치 글과 같은 틀 — 머리 글이 배치 소스에 그대로 있다", () => {
    const 배치 = readFileSync("../batch/services/desk.py", "utf-8");
    expect(배치).toContain('"판정 시점(126·189·252평가에만 판정): "');
    expect(배치).toContain("새 판 등록 전엔 통과 없음");
  });
});
