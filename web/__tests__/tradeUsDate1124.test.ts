import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("미국 종목 체결일은 현지 날짜 (docs/infra.md 25.1124)", () => {
  it("뉴욕에서 아직 오지 않은 날짜면 말한다", async () => {
    const { usLocalDateNote, newYorkDay } = await import("@/lib/portfolio");
    // 한국 2026-10-10(토) 02:30 = 뉴욕 2026-10-09(금) 13:30 (EDT)
    const 새벽 = new Date("2026-10-09T17:30:00Z");
    expect(newYorkDay(새벽)).toBe("2026-10-09");
    expect(usLocalDateNote("US", "2026-10-10", 새벽)).toContain("2026-10-09");
    expect(usLocalDateNote("US", "2026-10-09", 새벽)).toBeNull();
    expect(usLocalDateNote("KR", "2026-10-10", 새벽)).toBeNull();
    // 겨울(EST)도 Intl 이 따른다 — 한국 2027-01-05 13:00 = 뉴욕 2027-01-04 23:00
    expect(newYorkDay(new Date("2027-01-05T04:00:00Z"))).toBe("2027-01-04");
  });
  it("저장 경로가 부른다", () => {
    const 원문 = readFileSync(join(process.cwd(), "app", "api", "trades", "route.ts"), "utf-8");
    expect(원문).toContain("usLocalDateNote(stock.country, input.trade_date)");
    expect(원문).toContain("SELECT currency, country FROM stocks WHERE id = ?");
  });
});
