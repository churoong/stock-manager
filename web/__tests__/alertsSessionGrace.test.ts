import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * **알림 화면도 폐장 뒤 유예까지 같은 세션으로 본다** (docs/infra.md 25.279).
 * 장중 경로(`activeSession`)는 폐장 + 30분(25.724, 전에는 25분)까지 장중인데, 알림 경로는 폐장 시각이 지나면 다음 세션을 골랐다.
 */
const 받은: unknown[][] = [];

beforeEach(() => {
  받은.length = 0;
  vi.resetModules();
  vi.doMock("@/lib/db", async (orig) => ({
    ...(await (orig as () => Promise<Record<string, unknown>>)()),
    execute: async (sql: string, args: unknown[] = []) => {
      if (/FROM market_sessions/.test(sql)) 받은.push(args);
      return { columns: [], rows: [], affectedRows: 0 };
    },
  }));
  vi.useFakeTimers({ now: new Date("2026-10-19T06:40:00Z"), toFake: ["Date"] }); // 15:40 KST
});

afterEach(() => {
  vi.doUnmock("@/lib/db");
  vi.useRealTimers();
});

describe("알림 화면의 세션 고르기", () => {
  it("폐장 30분 뒤까지는 오늘 세션을 고른다 (25.724)", async () => {
    const { GET } = await import("@/app/api/alerts/route");
    await GET();
    expect(받은[0]?.[0]).toBe("2026-10-19T06:10:00.000Z");
  });
});
