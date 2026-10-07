import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * **보유 화면의 매도 플래그가 조용히 사라지지 않는다** (docs/infra.md 25.223).
 *
 * `ACTIVE_FLAGS` 를 `.catch(() => 빈 결과)` 로 읽어, 한도·인증 실패도 "오늘 플래그 없음" 과 똑같이 보였다.
 * 표가 없을 때만 조용하고, 그 밖의 실패는 빈 목록 + 경고다(경로 전체를 실패시키면 보유 화면이 통째로 안 보인다).
 */

function rs(columns: string[] = [], rows: unknown[][] = []) {
  return { columns, rows, affectedRows: 0 };
}

async function 부르기(플래그_실패: string | null) {
  vi.doMock("@/lib/db", async (orig) => ({
    ...(await (orig as () => Promise<Record<string, unknown>>)()),
    execute: async (sql: string) => {
      if (/FROM sell_flags/.test(sql) && 플래그_실패) throw new Error(플래그_실패);
      if (/version/i.test(sql)) return rs(["version"], [["t1:x|d0:"]]);
      return rs();
    },
  }));
  const { GET } = await import("@/app/api/portfolio/route");
  return (await GET()).json() as Promise<{ flags: unknown[]; warnings: string[] }>;
}

describe("매도 플래그 읽기 실패", () => {
  beforeEach(() => vi.resetModules());
  afterEach(() => vi.doUnmock("@/lib/db"));

  it("표가 없으면 조용하다", async () => {
    const body = await 부르기("no such table: sell_flags");
    expect(body.flags).toEqual([]);
    expect(body.warnings.join()).not.toContain("매도 플래그");
  });

  it("다른 실패는 빈 목록에 경고를 붙인다", async () => {
    const body = await 부르기("D1 일일 읽기 한도 초과");
    expect(body.flags).toEqual([]);
    expect(body.warnings.join()).toContain("매도 플래그를 읽지 못했습니다");
  });

  it("미끼 — 실패가 없으면 경고도 없다", async () => {
    const body = await 부르기(null);
    expect(body.warnings.join()).not.toContain("매도 플래그");
  });
});
