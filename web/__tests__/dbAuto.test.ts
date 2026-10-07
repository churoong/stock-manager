/**
 * DB_BACKEND=auto (docs/infra.md 25.12). 10월 1일 자동 복귀를 위해 웹이 스스로 백엔드를 고른다.
 * 네트워크는 가짜다: Turso 와 D1 의 응답 모양만 흉내 낸다.
 *
 * 판정의 중심은 D1 의 **복귀 표시**다. Turso 가 살아나도 복귀 스크립트가 표와 매매를 옮기기
 * 전에는 D1 을 쓴다 (2026-09-18, 리셋 직후 옛 스키마의 Turso 를 읽는 틈을 막았다).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const BLOCKED = {
  results: [{ type: "error", error: { message: "Operation was blocked: SQL read operations are forbidden (reads are blocked)" } }],
};
const TURSO_OK = {
  results: [{ type: "ok", response: { type: "execute", result: { cols: [{ name: "n" }], rows: [[{ type: "integer", value: "7" }]] } } }],
};
const d1Rows = (rows: Array<Record<string, unknown>>) => ({ success: true, result: [{ success: true, results: rows, meta: {} }] });

/** d1Marker: true·false 는 복귀 표시 유무, "down" 은 D1 이 응답하지 않는 경우 */
function fakeNetwork(turso: object, d1Marker: boolean | "down") {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? "{}"));
      if (url.includes("turso")) {
        calls.push("turso");
        return new Response(JSON.stringify(turso), { status: 200 });
      }
      // 표시 이름은 SQL 이 아니라 파라미터로 온다
      const isMarker = JSON.stringify(body.params ?? []).includes("db_return_done_at");
      calls.push(isMarker ? "d1:marker" : `d1:${body.sql ?? "batch"}`);
      if (d1Marker === "down") {
        return new Response(JSON.stringify({ success: false, errors: [{ code: 7500, message: "internal" }] }), { status: 503 });
      }
      if (isMarker) {
        return new Response(JSON.stringify(d1Rows(d1Marker ? [{ value: '"t"' }] : [])), { status: 200 });
      }
      return new Response(JSON.stringify(d1Rows([{ n: 42 }])), { status: 200 });
    }),
  );
  return calls;
}

beforeEach(() => {
  vi.resetModules();
  process.env.DB_BACKEND = "auto";
  process.env.TURSO_DATABASE_URL = "libsql://db.turso.io";
  process.env.TURSO_AUTH_TOKEN = "t";
  process.env.D1_ACCOUNT_ID = "a";
  process.env.D1_DATABASE_ID = "b";
  process.env.D1_API_TOKEN = "c";
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.DB_BACKEND;
});

// 판정표는 여기 있었다가 `__tests__/dbBackend.test.ts` 로 옮겼다 (2026-09-21).
// 파이썬 테스트가 **같은 표를 손으로 베껴** 갖고 있었고, 한쪽을 고치면 다른 쪽은 옛 표를
// 지키며 조용히 통과했다. 이제 둘 다 tests/fixtures/db_backend_decision.json 을 읽는다.
// 여기 남은 것은 판정 **뒤에** 일어나는 일들이다 (docs/infra.md 25.42).

describe("auto 경로", () => {
  it("복귀 표시가 없으면 D1 에서 읽는다 — Turso 는 살펴보지도 않는다", async () => {
    const calls = fakeNetwork(BLOCKED, false);
    const { execute, rowsToObjects } = await import("@/lib/db");
    const rows = rowsToObjects<{ n: number }>(await execute("SELECT 42 AS n"));
    expect(rows).toEqual([{ n: 42 }]);
    expect(calls).toEqual(["d1:marker", "d1:SELECT 42 AS n"]);
  });

  it("Turso 가 살아나도 복귀 표시가 없으면 D1 에서 읽는다 (리셋 직후 ~ 복귀 워크플로 사이)", async () => {
    const calls = fakeNetwork(TURSO_OK, false);
    const { execute } = await import("@/lib/db");
    await execute("SELECT 42 AS n");
    expect(calls).not.toContain("turso");
    expect(calls.at(-1)).toBe("d1:SELECT 42 AS n");
  });

  it("복귀 표시가 있으면 Turso 에서 읽는다", async () => {
    const calls = fakeNetwork(TURSO_OK, true);
    const { execute } = await import("@/lib/db");
    await execute("SELECT 1");
    expect(calls).toEqual(["d1:marker", "turso"]);
  });

  it("복귀 표시가 있으면 Turso 가 막혀도 D1 로 튀지 않는다", async () => {
    fakeNetwork(BLOCKED, true);
    const { execute } = await import("@/lib/db");
    await expect(execute("SELECT 1")).rejects.toThrow(/한도/);
  });

  it("D1 을 못 읽으면 Turso 상태로 정하고, 그 판정은 짧게만 믿는다", async () => {
    const calls = fakeNetwork(TURSO_OK, "down");
    const { execute } = await import("@/lib/db");
    await execute("SELECT 1");
    expect(calls).toEqual(["d1:marker", "turso", "turso"]); // 표시 읽기 실패 → 살펴보기 → 실제 질의
    vi.useFakeTimers();
    try {
      vi.advanceTimersByTime(6_000);
      await execute("SELECT 1");
      expect(calls.filter((c) => c === "d1:marker")).toHaveLength(2); // 5초 뒤 다시 본다
    } finally {
      vi.useRealTimers();
    }
  });

  it("판정은 잠시 재사용한다 — 요청마다 D1 을 찔러 보지 않는다", async () => {
    const calls = fakeNetwork(BLOCKED, false);
    const { execute } = await import("@/lib/db");
    await execute("SELECT 42 AS n");
    await execute("SELECT 42 AS n");
    expect(calls.filter((c) => c === "d1:marker")).toHaveLength(1);
  });
});
