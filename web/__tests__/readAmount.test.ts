/** 쉼표 넣은 수수료·세금·환율이 말없이 "비움" 으로 저장됐다 (docs/infra.md 25.642, 감사). */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { readAmount, unreadableAmount } from "@/lib/portfolio";

describe("readAmount", () => {
  it("쉼표·공백을 빼고 읽는다", () => {
    expect(readAmount("1,500")).toBe(1500);
    expect(readAmount(" 1,385.2 ")).toBe(1385.2);
    expect(readAmount("")).toBeNull();
    expect(readAmount("  ")).toBeNull();
  });

  it("모호한 쉼표·지수·16진 표기는 못 읽은 것으로 (25.645)", () => {
    for (const 글 of ["1,5", "1.380,5", "12,34,567", "1e3", "0x10", "1.500원"]) expect(readAmount(글)).toBeNaN();
    expect(readAmount("1,234,567.5")).toBe(1234567.5);
    expect(readAmount("0.5")).toBe(0.5);
    expect(readAmount("-3")).toBe(-3); // 음수는 서버가 막는다
  });

  it("숨은 칸은 보지 않는다 (25.645)", () => {
    const 폼 = readFileSync("components/PortfolioView.tsx", "utf8");
    expect(폼).toContain('...(side === "sell" ? { 세금: tax } : {})');
    expect(폼.match(/stock\.currency !== "KRW" \? \{ 환율: fx \}/g)?.length).toBe(2);
  });

  it("못 읽으면 저장 전에 막는다 — 빈칸은 비움으로 둔다", () => {
    expect(unreadableAmount({ 수수료: "1,500", 세금: "" })).toBeNull();
    expect(unreadableAmount({ 수수료: "1.500원" })).toBe('수수료 "1.500원" 를 숫자로 읽지 못했습니다');
  });

  it("매매·배당 폼이 숫자 칸(type=number)을 쓰지 않고 막는 판정을 부른다", () => {
    const 폼 = readFileSync("components/PortfolioView.tsx", "utf8");
    expect(폼).not.toMatch(/type="number" inputMode="decimal" value=\{(price|qty|fx|fee|tax|gross)\}/);
    expect(폼.match(/unreadableAmount\(/g)?.length).toBe(2);
  });
});
