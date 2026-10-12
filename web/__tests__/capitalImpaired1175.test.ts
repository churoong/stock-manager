import { describe, expect, it } from "vitest";
import { capitalImpairedNote } from "@/lib/stockDetail";

/** 자본잠식 표시 — 문서가 약속한 "화면에 드러낸다" (docs/factors.md 3.2, 25.1175) */
describe("자본잠식 표시", () => {
  it("적자면 ROE 도, 흑자면 부채비율만 최악이라 말한다", () => {
    expect(capitalImpairedNote({ _capital_impaired: { roe_worst: true } })).toContain("부채비율·ROE");
    expect(capitalImpairedNote({ _capital_impaired: { roe_worst: false } })).toContain("ROE 는 뺌");
    expect(capitalImpairedNote({ roe: 0.1 })).toBe("");
  });
});
