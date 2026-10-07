import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { MIN_NAME_LENGTH, unmatchedNameNote } from "@/lib/newsKr";

/** 두 글자 이름 종목에 "기사 5건 미만" 이라 적어 매칭하지 않는 이유를 가렸다 (docs/infra.md 25.747, 감사) */
describe("짧은 이름 안내", () => {
  it("국내 두 글자 이름만 안내한다", () => {
    expect(MIN_NAME_LENGTH).toBe(3);
    expect(unmatchedNameNote("KR", "기아")).toContain("매칭하지 않습니다");
    expect(unmatchedNameNote("KR", "삼성전자")).toBeNull();
    expect(unmatchedNameNote("US", "GE")).toBeNull();
    // 한글 이름이 없으면 매칭되지 않으므로 그렇다고 알린다 (25.748)
    expect(unmatchedNameNote("KR", null)).toContain("한글 종목 이름이 없어");
    expect(unmatchedNameNote("KR", "")).toContain("한글 종목 이름이 없어");
  });
  it("종목 상세 뉴스 칸이 안내를 보여 준다", () => {
    const src = readFileSync("components/StockDetail.tsx", "utf8");
    expect(src).toContain("nameNote={unmatchedNameNote(String(stock.country ?? \"\"), stock.name_ko");
    expect(src).toContain("{nameNote ?");
  });
});
