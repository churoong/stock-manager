/** 관심 등록·해제 도우미 (Step 26). 던지지 않고 값으로 돌려준다. */
import { describe, expect, it } from "vitest";
import { addWatch, loadWatchIds, removeWatch } from "@/lib/watch";

function fakeFetch(handler: (url: string, init?: RequestInit) => { status: number; body?: unknown }) {
  return (async (url: string, init?: RequestInit) => {
    const r = handler(url, init);
    return { ok: r.status < 300, status: r.status, json: async () => r.body } as Response;
  }) as unknown as typeof fetch;
}

describe("관심 종목", () => {
  it("등록은 POST /api/watchlist 에 stock_id 만", async () => {
    let seen: { url: string; body: unknown } | null = null;
    const f = fakeFetch((url, init) => {
      seen = { url, body: JSON.parse(String(init?.body)) };
      return { status: 200, body: { ok: true } };
    });
    expect(await addWatch(7, f)).toEqual({ ok: true, error: null });
    expect(seen).toEqual({ url: "/api/watchlist", body: { stock_id: 7 } });
  });

  it("해제는 DELETE /api/watchlist/{id}", async () => {
    let url = "";
    const f = fakeFetch((u) => {
      url = u;
      return { status: 200, body: { ok: true } };
    });
    expect((await removeWatch(3, f)).ok).toBe(true);
    expect(url).toBe("/api/watchlist/3");
  });

  it("실패는 서버가 준 이유를 값으로", async () => {
    const f = fakeFetch(() => ({ status: 500, body: { errors: ["no such table: watchlist"] } }));
    expect(await addWatch(1, f)).toEqual({ ok: false, error: "no such table: watchlist" });
  });

  it("목록은 종목 id → 관심 id 이고 못 읽으면 null(모름, 25.815)", async () => {
    const f = fakeFetch(() => ({ status: 200, body: { watchlist: [{ id: 11, stock_id: 1 }, { id: 12, stock_id: 5 }] } }));
    const m = await loadWatchIds(f);
    expect(m?.get(5)).toBe(12);
    expect(await loadWatchIds(fakeFetch(() => ({ status: 401 })))).toBeNull();
  });

  it("이미 빠진 관심 종목을 지우면 성공으로 본다 (25.817)", async () => {
    const { WATCH_GONE } = await import("@/lib/watch");
    expect(await removeWatch(3, fakeFetch(() => ({ status: 404, body: { errors: [WATCH_GONE] } })))).toEqual({ ok: true, error: null });
    expect((await removeWatch(3, fakeFetch(() => ({ status: 500, body: { errors: ["x"] } })))).ok).toBe(false);
  });
});
