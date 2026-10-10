/** 달력이 KIS 와 어긋난 날 장중은 국내 시세를 야후로만 받는다 (docs/infra.md 25.1086) */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { kisCalendarSuspect } from "@/lib/intraday";

describe("KIS 달력 불일치", () => {
  it("남긴 날짜에 오늘이 있으면 의심, 못 읽으면 예전처럼 KIS", () => {
    const v = JSON.stringify({ checked: "2026-10-07", dates: ["2026-10-08"] });
    expect(kisCalendarSuspect(v, "2026-10-08")).toBe(true);
    expect(kisCalendarSuspect(v, "2026-10-09")).toBe(false);
    expect(kisCalendarSuspect(null, "2026-10-08")).toBe(false);
    expect(kisCalendarSuspect("{깨짐", "2026-10-08")).toBe(false);
  });
  it("경로가 시세를 고르기 전에 본다", () => {
    const src = readFileSync("app/api/cron/intraday/route.ts", "utf8");
    expect(src).toContain("kisCalendarSuspect(어긋남, day)");
    expect(src).toContain("fetchQuotes(symbols, market, now, session.date)");
  });
});
