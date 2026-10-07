import { describe, expect, it } from "vitest";
import { etfNameExcludedNote } from "@/lib/etf";

/** 이름으로 뺀 미국 ETF 는 판정·저장이 없어 "왜 TQQQ 가 없나" 에 답이 없었다 (docs/infra.md 25.718) */
describe("이름으로 뺀 ETF", () => {
  it("실행 기록의 기호를 한 줄로 알린다", () => {
    expect(etfNameExcludedNote(JSON.stringify({ name_excluded: ["SQQQ", "TQQQ"] }))).toBe(
      "이름으로 레버리지·인버스라 보고 판정 전에 뺀 ETF 2개 (예: TQQQ, SQQQ)",
    );
  });
  it("잘 알려진 상품을 먼저 보이고 개수는 정확히 (25.722)", () => {
    const 목록 = [...Array.from({ length: 400 }, (_, i) => `A${String(i).padStart(3, "0")}`), "SQQQ", "TQQQ"];
    const 줄 = etfNameExcludedNote(JSON.stringify({ name_excluded: 목록 }))!;
    expect(줄).toContain("402개 (예: TQQQ, SQQQ");
    expect(줄).not.toContain("이상");
  });
  it("없거나 못 읽으면 말하지 않는다", () => {
    expect(etfNameExcludedNote(JSON.stringify({}))).toBeNull();
    expect(etfNameExcludedNote("{깨짐")).toBeNull();
  });
});
