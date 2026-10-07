import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * **틀린 본문이 모든 알림을 바꾸지 않는가** (docs/infra.md 25.312).
 * 예전에는 숫자 아닌 id 를 버린 뒤 빈 목록 → "안 읽은 전부" 경로로 떨어졌다.
 */
const 문장: string[] = [];

beforeEach(() => {
  문장.length = 0;
  vi.resetModules();
  vi.doMock("@/lib/db", async (orig) => ({
    ...(await (orig as () => Promise<Record<string, unknown>>)()),
    batch: async (stmts: Array<{ sql: string }>) => {
      문장.push(...stmts.map((s) => s.sql));
      return stmts.map(() => ({ columns: [], rows: [], affectedRows: 1 }));
    },
  }));
});

afterEach(() => {
  vi.doUnmock("@/lib/db");
});

async function patch(body: unknown) {
  const { PATCH } = await import("@/app/api/alerts/route");
  return PATCH(
    new Request("https://x/api/alerts", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  );
}

describe("PATCH /api/alerts", () => {
  it("문자열 id 는 400 이고 아무것도 바꾸지 않는다", async () => {
    const res = await patch({ ids: ["123"] });
    expect(res.status).toBe(400);
    expect(문장).toEqual([]);
  });

  it("read 가 불리언이 아니면 400", async () => {
    expect((await patch({ ids: [1], read: "false" })).status).toBe(400);
  });

  it("빈 목록은 '모두 읽음' 이다 (화면이 이렇게 보낸다)", async () => {
    const res = await patch({ ids: [], read: true });
    expect(res.status).toBe(200);
    expect(문장[0]).toContain("WHERE is_read = 0");
  });

  it("숫자 id 는 그 건만", async () => {
    await patch({ ids: [7] });
    expect(문장[0]).toContain("WHERE id IN (?)");
  });
});
