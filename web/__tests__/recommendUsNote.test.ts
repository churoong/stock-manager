import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * **미국 추천이 비었을 때 박아 둔 글 대신 지금 상태를 말하는가** (docs/infra.md 25.249).
 *
 * 예전에는 미국이면 늘 "유니버스에 든 종목이 없다 · SEC 재무 수집 뒤에 나온다" 였다 — 미국 재무 경로가 생긴 뒤에도,
 * 미국 신호가 정말 멈춰도. 이제 D1 동안(설계상 쉰다)만 그렇게 말하고, 그 밖에는 국내와 같은 진단을 탄다.
 */

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;
let backend: "turso" | "d1" = "turso";

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS)
    .filter((f) => f.endsWith(".sql"))
    .sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  vi.resetModules();
  vi.doMock("@/lib/db", async (orig) => {
    const 진짜 = await (orig as () => Promise<Record<string, unknown>>)();
    return {
      ...진짜,
      currentBackend: async () => backend,
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
});

async function 미국_추천() {
  const { POST } = await import("@/app/api/recommend/route");
  const res = await POST(
    new Request("https://x/api/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ country: "US" }),
    }),
  );
  return (await res.json()) as { notes: string[] };
}

describe("미국 추천이 비었을 때", () => {
  it("D1 동안은 쉰다고 말한다", async () => {
    backend = "d1";
    const { notes } = await 미국_추천();
    expect(notes.join(" ")).toContain("쉬고 있어");
  });

  it("그 밖에는 박아 둔 글 대신 재료를 읽어 진단한다", async () => {
    backend = "turso";
    const { notes } = await 미국_추천();
    const 글 = notes.join(" ");
    expect(글).not.toContain("SEC 공시");
    expect(글).toContain("신호를 계산한 적이 없습니다"); // 빈 DB — 신호 실행 기록이 0
  });
});
