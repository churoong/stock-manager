/** 매도 플래그 확인: 옛 번호로 누르면 성공처럼 굴지 않는다 (docs/infra.md 25.554, 감사 재현). */

import { afterEach, describe, expect, it, vi } from "vitest";

function 가짜(바뀐: number, 있음: boolean) {
  vi.doMock("@/lib/db", () => ({
    execute: async (sql: string) =>
      sql.startsWith("UPDATE")
        ? { columns: [], rows: [], affectedRows: 바뀐 }
        : { columns: ["dismissed_at"], rows: 있음 ? [["t"]] : [], affectedRows: 0 },
  }));
}

describe("매도 플래그 확인", () => {
  afterEach(() => {
    vi.doUnmock("@/lib/db");
    vi.resetModules();
  });

  const 부르기 = async () => {
    const { POST } = await import("@/app/api/sell-flags/[id]/route");
    return POST(new Request("http://x", { method: "POST" }), { params: Promise.resolve({ id: "7" }) });
  };

  it("다시 계산돼 사라진 번호면 409 와 까닭", async () => {
    vi.resetModules();
    가짜(0, false);
    const r = await 부르기();
    expect(r.status).toBe(409);
    expect(((await r.json()) as { errors: string[] }).errors[0]).toContain("다시 계산");
  });

  it("이미 확인된 것은 성공", async () => {
    vi.resetModules();
    가짜(0, true);
    const r = await 부르기();
    expect(r.status).toBe(200);
  });
});
