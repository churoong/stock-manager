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

// **auto 라도 D1 으로 넘어가지 않는다** (2026-10-08 사용자 결정 "D1으로 넘어가지말자", docs/infra.md 25.1030).
// 예전(25.12) 시험은 복귀 표시로 D1 을 고르는 길을 봤다 — 판정표 자체(`decideBackend`)는 dbBackend.test.ts 가 그대로 본다
describe("auto 경로 (25.1030)", () => {
  it("Turso 가 한도로 막혀도 D1 을 찔러 보지 않고 Turso 로 간다 — 한도 오류가 그대로 올라온다", async () => {
    const calls = fakeNetwork(BLOCKED, false);
    const { execute, AUTO_FALLS_BACK_TO_D1 } = await import("@/lib/db");
    expect(AUTO_FALLS_BACK_TO_D1).toBe(false);
    await expect(execute("SELECT 42 AS n")).rejects.toThrow();
    expect(calls.every((c) => c === "turso")).toBe(true);
  });

  it("Turso 가 살아 있으면 Turso 에서 읽는다 — 복귀 표시를 보지 않는다", async () => {
    const calls = fakeNetwork(TURSO_OK, false);
    const { execute, rowsToObjects } = await import("@/lib/db");
    expect(rowsToObjects<{ n: number }>(await execute("SELECT 7 AS n"))).toEqual([{ n: 7 }]);
    expect(calls).toEqual(["turso"]);
  });

  it("사람이 DB_BACKEND=d1 이라고 적으면 그때만 D1", async () => {
    process.env.DB_BACKEND = "d1";
    const calls = fakeNetwork(BLOCKED, false);
    const { execute } = await import("@/lib/db");
    await execute("SELECT 42 AS n");
    expect(calls).toEqual(["d1:SELECT 42 AS n"]);
  });
});

describe("배치와 같은 결정 (25.1030)", () => {
  it("배치도 auto 에서 D1 으로 넘어가지 않는다", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const py = readFileSync(join(process.cwd(), "..", "batch", "core", "client.py"), "utf-8");
    expect(py).toContain("AUTO_FALLS_BACK_TO_D1 = False");
  });
});
