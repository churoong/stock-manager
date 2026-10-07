/** 상태 화면이 D1 에서 쉬는 칸을 읽지 않는다 — 한 번 열 때 약 325만 행을 읽었다 (docs/infra.md 25.857, 감사) */
import { describe, expect, it, vi } from "vitest";
import { FRESHNESS, freshnessVerdict } from "@/lib/health";

async function 묶음에_간_질의(backend: string): Promise<{ sqls: string[]; freshness: Array<{ key: string; value: string | null }> }> {
  vi.resetModules();
  const sqls: string[] = [];
  const 빈 = { columns: ["v"] as string[], rows: [["2026-09-30"]] as unknown[][] };
  vi.doMock("@/lib/db", () => ({
    currentBackend: async () => backend,
    rowsToObjects: (rs: { columns: string[]; rows: unknown[][] }) =>
      rs.rows.map((r) => Object.fromEntries(rs.columns.map((c, i) => [c, r[i]]))),
    batch: async (stmts: Array<{ sql: string }>) => {
      sqls.push(...stmts.map((s) => s.sql));
      return stmts.map(() => 빈);
    },
    execute: async () => ({ columns: [], rows: [] }),
  }));
  try {
    const { GET } = await import("@/app/api/status/route");
    const body = (await (await GET()).json()) as { freshness: Array<{ key: string; value: string | null }> };
    return { sqls, freshness: body.freshness };
  } finally {
    vi.doUnmock("@/lib/db");
  }
}

describe("상태 화면 신선도 (25.857)", () => {
  const 쉬는 = FRESHNESS.filter((f) => f.restsOnD1);

  it("D1 이면 쉬는 칸의 질의를 보내지 않고, 칸은 그대로 있다", async () => {
    const { sqls, freshness } = await 묶음에_간_질의("d1");
    expect(쉬는.length).toBeGreaterThan(5);
    for (const f of 쉬는) expect(sqls).not.toContain(f.sql);
    expect(freshness.length).toBe(FRESHNESS.length);
    expect(freshness.find((f) => f.key === 쉬는[0].key)?.value).toBeNull();
    expect(freshnessVerdict(쉬는[0].key, null, new Date(), "d1").why).toContain("읽지 않습니다");
  });

  it("Turso 면 모두 읽는다", async () => {
    const { sqls } = await 묶음에_간_질의("turso");
    for (const f of FRESHNESS) expect(sqls).toContain(f.sql);
  });
});
