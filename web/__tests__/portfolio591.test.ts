import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { LIST_LIMIT, capList, fxDateNote, qtyText } from "@/lib/portfolio";

describe("포트폴리오 감사 (docs/infra.md 25.591)", () => {
  it("소수 수량을 셋째 자리에서 반올림하지 않고, 부동소수 찌꺼기는 자른다", () => {
    expect(qtyText(0.123456)).toBe("0.123456");
    expect(qtyText(0.1 + 0.2)).toBe("0.3");
    expect(qtyText(1200)).toBe("1,200");
    expect(qtyText(0.3 - 0.1 - 0.2), "-0 이 아니다 (25.594)").toBe("0");
  });

  it("목록은 상한까지 자르고 잘렸다고 알린다", () => {
    const 긴 = Array.from({ length: LIST_LIMIT + 1 }, (_, i) => i);
    expect(capList(긴)).toEqual({ rows: 긴.slice(0, LIST_LIMIT), truncated: true });
    expect(capList([1, 2]).truncated).toBe(false);
  });

  it("자동 환율이 그날 것이 아니면 어느 날 종가인지 말한다", () => {
    expect(fxDateNote("auto", { date: "2026-09-25" }, "2026-09-26")).toContain("2026-09-25 종가");
    expect(fxDateNote("auto", { date: "2026-09-26" }, "2026-09-26")).toBeNull();
    expect(fxDateNote("manual", { date: "2026-09-25" }, "2026-09-26")).toBeNull();
  });

  it("화면: 늦은 응답은 버리고, 설정을 못 읽은 것을 세율 없음으로 말하지 않는다", () => {
    const 화면 = readFileSync("components/PortfolioView.tsx", "utf8");
    expect(화면).toContain("if (번호 !== 불러오기번호.current) return;");
    expect(화면).toContain("setTaxesUnread(!st.ok);");
    expect(화면).toContain("설정을 읽지 못해 세율을 채우지 못했습니다");
    expect(화면).not.toMatch(/quantity(_sold|_bought)?\.toLocaleString\(\)/);
  });

  it("삭제가 파생 표를 지운 뒤 실패하면 재계산을 부른다", () => {
    const 경로 = readFileSync("app/api/trades/[id]/route.ts", "utf8");
    expect(경로).toContain("const recalc = 파생지움 ? await requestRecalc().catch(() => null) : null;");
  });
});

it("수량 글이 남은 자리도 qtyText 를 쓴다 (25.594)", () => {
  expect(readFileSync("components/StockDetail.tsx", "utf8")).toContain("{qtyText(Number(position.quantity))}주");
  expect(readFileSync("app/api/trades/route.ts", "utf8")).toContain("${qtyText(뒤.short)}주");
});

