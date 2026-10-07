/** 실패로 끝난 ETF 판정을 화면이 말한다 (docs/infra.md 25.826) */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { etfFailedNote } from "@/lib/etf";

describe("ETF 판정 실패 안내", () => {
  it("마지막 성공보다 뒤의 실패만 말한다", () => {
    const 실패 = { started_at: "2026-10-06T01:00:00Z", error_text: "D1 시간 초과" };
    expect(etfFailedNote(실패, "2026-09-06T02:00:00Z")).toContain("가장 최근 ETF 판정이 실패했습니다(2026-10-06 시작: D1 시간 초과)");
    expect(etfFailedNote(실패, "2026-10-06T03:00:00Z")).toBeNull();
    expect(etfFailedNote(실패, null)).toContain("실패했습니다");
    expect(etfFailedNote(undefined, "2026-09-06")).toBeNull();
  });

  it("핵심·위성 두 경로가 읽는다", () => {
    expect(readFileSync("app/api/etf/route.ts", "utf-8")).toContain("note: 실패말 ?? etfRunNote(");
    expect(readFileSync("app/api/etf/satellite/route.ts", "utf-8")).toContain("if (실패말) notes.push(");
  });
});

describe("ETF 실패 안내 다듬기 (25.831)", () => {
  it("실패만 있는 시장 줄은 마지막 판정 고르기에서 빠지고, 위성은 시험 실행을 세지 않는다", () => {
    const 경로 = readFileSync("app/api/etf/route.ts", "utf-8");
    expect(경로).toContain("r !== null && Boolean(r.finished_at)");
    expect(경로).toContain("ETF 판정이 아직 한 번도 성공하지 못했습니다");
    expect(readFileSync("app/api/etf/satellite/route.ts", "utf-8")).toContain("NOT LIKE '%--only%'");
  });
});
