import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * **리포트 경로가 빠진 리포트를 정규장으로 세고, 가장 새 리포트에만 붙이는가** (docs/infra.md 25.235).
 *
 * 예전 화면은 거래일과 UTC 오늘의 달력 차이가 2 이상이면 "N일 지난 리포트 — 그 뒤로 새 리포트가 없습니다" 를 띄웠다.
 * 월요일 아침 리포트(거래일 금요일)는 나오자마자, 날짜를 골라 본 옛 리포트는 늘 그 말을 들었다.
 * 실제 스키마의 SQLite 에 경로의 질의를 그대로 흘린다.
 */

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS)
    .filter((f) => f.endsWith(".sql"))
    .sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  for (const d of ["2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21"]) {
    db.exec(`INSERT INTO market_sessions (market, date, open_utc, close_utc, source)
      VALUES ('KR', '${d}', '${d}T00:00:00Z', '${d}T06:30:00Z', 't')`);
  }
  for (const [id, trade] of [
    [1, "2026-10-15"],
    [2, "2026-10-16"],
  ] as const) {
    db.exec(`INSERT INTO daily_reports (id, market, trade_date, status, generated_at, summary_text, warnings_json)
      VALUES (${id}, 'KR', '${trade}', 'success', '${trade}T23:30:00Z', '글', '[]')`);
  }

  vi.resetModules();
  vi.doMock("@/lib/db", async (orig) => {
    const 진짜 = await (orig as () => Promise<Record<string, unknown>>)();
    return {
      ...진짜,
      execute: async (sql: string, args: unknown[] = []) => {
        const stmt = db.prepare(sql);
        const rows = stmt.all(...(args as never[])) as Array<
          Record<string, unknown>
        >;
        const columns = rows[0]
          ? Object.keys(rows[0])
          : stmt.columns().map((c) => c.name);
        return {
          columns,
          rows: rows.map((r) => columns.map((c) => r[c])),
          affectedRows: 0,
        };
      },
    };
  });
});

afterEach(() => {
  vi.doUnmock("@/lib/db");
  vi.useRealTimers();
});

async function 읽기(query: string) {
  const { GET } = await import("@/app/api/reports/route");
  const res = await GET(new Request(`https://x/api/reports?${query}`));
  expect(res.status).toBe(200);
  return (await res.json()) as {
    report: { trade_date: string } | null;
    missed_reports: number | null;
  };
}

describe("빠진 리포트 세기", () => {
  it("월요일 아침에 막 나온 리포트(거래일 금요일)는 빠진 것이 없다", async () => {
    vi.useFakeTimers({
      now: new Date("2026-10-18T23:40:00Z"),
      toFake: ["Date"],
    }); // 월 08:40 KST
    const 답 = await 읽기("market=KR");
    expect(답.report?.trade_date).toBe("2026-10-16");
    expect(답.missed_reports).toBe(0);
  });

  it("다음 정규장이 두 번 열렸는데 새 리포트가 없으면 둘 빠졌다", async () => {
    vi.useFakeTimers({
      now: new Date("2026-10-21T01:00:00Z"),
      toFake: ["Date"],
    }); // 수 10:00 KST
    expect((await 읽기("market=KR")).missed_reports).toBe(2);
  });

  it("날짜를 골라 본 옛 리포트에는 붙이지 않는다", async () => {
    vi.useFakeTimers({
      now: new Date("2026-10-21T01:00:00Z"),
      toFake: ["Date"],
    });
    const 답 = await 읽기("market=KR&date=2026-10-15");
    expect(답.report?.trade_date).toBe("2026-10-15");
    expect(답.missed_reports).toBeNull();
  });
});
