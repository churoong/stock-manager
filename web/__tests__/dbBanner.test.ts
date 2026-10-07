/**
 * DB 상태 띠 (docs/infra.md 24절).
 *
 * 2026-09-18 사용자 보고: 한도로 막힌 동안 화면에 **메뉴만 보이고 데이터가 전부 비었다.**
 * 화면마다 "데이터 없음" 이라고만 적혀 고장인지 데이터가 없는 것인지 알 수 없었다.
 * 그래서 막힘을 한 곳에서 한국어로 말한다.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.resetModules();
  vi.restoreAllMocks();
});

describe("/api/db-health", () => {
  it("살아 있으면 ok", async () => {
    vi.doMock("@/lib/db", () => ({
      execute: vi.fn().mockResolvedValue({ columns: [], rows: [], affectedRows: 0 }),
      explain: (m: string) => m,
    }));
    const { GET } = await import("@/app/api/db-health/route");
    const body = await (await GET()).json();
    expect(body).toEqual({ ok: true });
  });

  it("막히면 한국어 사유를 돌려준다 (영어 원문만 보여 주지 않는다)", async () => {
    const raw = "Operation was blocked: SQL read operations are forbidden (reads are blocked)";
    // 앞 테스트의 모킹이 남아 있어 그냥 import 하면 가짜 explain 이 온다. 진짜를 직접 가져온다
    vi.doMock("@/lib/db", async () => ({
      ...(await vi.importActual<typeof import("@/lib/db")>("@/lib/db")),
      execute: vi.fn().mockRejectedValue(new Error(raw)),
    }));
    const { GET } = await import("@/app/api/db-health/route");
    const body = await (await GET()).json();
    expect(body.ok).toBe(false);
    expect(body.reason).toContain("월 읽기 한도");
    expect(body.reason).toContain("다음 결제 주기");
  });

  it("표를 읽지 않는다 — 막혔는지 보려고 읽기 예산을 쓰지 않는다", async () => {
    const execute = vi.fn().mockResolvedValue({ columns: [], rows: [], affectedRows: 0 });
    vi.doMock("@/lib/db", () => ({ execute, explain: (m: string) => m }));
    const { GET } = await import("@/app/api/db-health/route");
    await GET();
    expect(execute).toHaveBeenCalledWith("SELECT 1");
  });
});
