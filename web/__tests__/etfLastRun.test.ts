import { afterEach, describe, expect, it, vi } from "vitest";

/** ETF 화면의 마지막 배치 시각은 시장마다 따로다 (docs/infra.md 25.354) */
describe("ETF 마지막 배치 시각", () => {
  afterEach(() => {
    vi.doUnmock("@/lib/db");
    vi.resetModules();
  });

  it("국내 판정이 없고 미국만 돌았으면 국내 칸은 비어 있다", async () => {
    const calls: Array<{ sql: string; args: unknown[] }> = [];
    vi.doMock("@/lib/db", async (orig) => ({
      ...(await (orig as () => Promise<Record<string, unknown>>)()),
      execute: async (sql: string, args: unknown[] = []) => {
        calls.push({ sql, args });
        if (/FROM batch_runs/.test(sql)) {
          const rows =
            args[0] === "US" ? [["2026-09-01T12:00:00Z", "success"]] : [];
          return { columns: ["finished_at", "status"], rows, affectedRows: 0 };
        }
        return { columns: [], rows: [], affectedRows: 0 };
      },
    }));
    const { POST } = await import("@/app/api/etf/route");
    const body = await (await POST()).json();
    expect(body.last_runs.KR).toBeNull();
    expect(body.last_runs.US.finished_at).toBe("2026-09-01T12:00:00Z");
    expect(
      calls
        .filter((c) => /FROM batch_runs/.test(c.sql))
        .every((c) => /market = \?/.test(c.sql)),
    ).toBe(true);
  });

  it("시험 실행(--only)은 마지막 배치로 치지 않는다 (25.662)", async () => {
    vi.doMock("@/lib/db", async (orig) => ({
      ...(await (orig as () => Promise<Record<string, unknown>>)()),
      execute: async (sql: string, args: unknown[] = []) => {
        if (/FROM batch_runs/.test(sql) && args[0] === "US") {
          return {
            columns: ["finished_at", "status", "step_log"],
            rows: [
              ["2026-09-20T12:00:00Z", "success", '{"args": "--only VOO"}'],
              ["2026-09-01T12:00:00Z", "success", "{}"],
            ],
            affectedRows: 0,
          };
        }
        return { columns: [], rows: [], affectedRows: 0 };
      },
    }));
    const { POST } = await import("@/app/api/etf/route");
    const body = await (await POST()).json();
    expect(body.last_runs.US.finished_at).toBe("2026-09-01T12:00:00Z");
  });
});
