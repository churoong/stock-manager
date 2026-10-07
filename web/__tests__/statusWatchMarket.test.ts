import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

/** /status 의 무응답 감시 표도 시장까지 맞춘다 — 텔레그램 감시와 같은 함수 (docs/infra.md 25.373) */
describe("상태 화면 무응답 감시의 마지막 성공", () => {
  it("작업 이름만으로 고르지 않는다", () => {
    const src = readFileSync("app/api/status/route.ts", "utf8");
    expect(src).not.toContain("lastSuccess.find((r) => r.job_name === w.job)");
    expect(src).toContain("findLastSuccess(lastSuccess, w)");
  });
});

/** 달력이 오늘까지 오지 않으면 모름, D1 이면 미국은 쉼 (docs/infra.md 25.552, 감사 재현) */
describe("상태 화면: 달력 없음과 D1 쉼", () => {
  it("달력이 끊기면 휴장이 아니라 null, D1 이면 미국은 무응답이 아니다", async () => {
    const { vi } = await import("vitest");
    vi.resetModules();
    const 빈 = { columns: [] as string[], rows: [] as unknown[][] };
    vi.doMock("@/lib/db", () => ({
      currentBackend: async () => "d1",
      rowsToObjects: (rs: { columns: string[]; rows: unknown[][] }) =>
        rs.rows.map((r) => Object.fromEntries(rs.columns.map((c, i) => [c, r[i]]))),
      batch: async () => [],
      execute: async (sql: string) => {
        if (sql.includes("FROM market_sessions")) {
          // 국내 달력은 지난 금요일에서 끝났고, 미국은 먼 날까지 있다
          return { columns: ["market", "date", "open_utc"], rows: [
            ["KR", "2026-09-25", "2026-09-25T00:00:00Z"],
            ["US", "2026-09-28", "2026-09-28T13:30:00Z"], ["US", "2026-10-30", "2026-10-30T13:30:00Z"],
          ] };
        }
        return 빈;
      },
    }));
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-28T22:00:00Z"));
    try {
      const { GET } = await import("@/app/api/status/route");
      const body = (await (await GET()).json()) as {
        watches: Array<{ market: string; trading_day: boolean | null; overdue: boolean; paused: boolean }>;
      };
      const kr = body.watches.filter((w) => w.market === "KR");
      const us = body.watches.filter((w) => w.market === "US");
      expect(kr.length).toBeGreaterThan(0);
      expect(kr.every((w) => w.trading_day === null)).toBe(true);
      expect(us.every((w) => w.paused && !w.overdue)).toBe(true);
    } finally {
      vi.useRealTimers();
      vi.doUnmock("@/lib/db");
    }
  });

  it("오늘 칸 말", async () => {
    const { dayNote } = await import("@/lib/health");
    expect(dayNote(null)).toContain("달력 없음");
    expect(dayNote(false)).toBe(" (휴장)");
    expect(dayNote(true)).toBe("");
    // D1 에 쉬는 미국은 달력이 말라도 "멈췄는지 확인" 이 아니다 (25.555)
    expect(dayNote(null, true)).toBe(" (D1 운영 중 쉼)");
  });
});
