/**
 * 메뉴를 오가도 **"불러오는 중" 없이** 이미 읽은 화면을 바로 그린다 (docs/infra.md 25.893, 2026-10-02 사용자 지적:
 * "다른 메뉴 들어갔다가 추천이나 적립으로 돌아가면 디비를 재조회하는거같은데?").
 */
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cachedFetch, clearClientCache, peekCachedJson } from "@/lib/clientCache";
import { loadWatchIds } from "@/lib/watch";

describe("이미 읽은 응답을 바로 꺼낸다", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    clearClientCache();
  });

  it("읽은 뒤에는 기다리지 않고 꺼내고, 없거나 지났으면 null", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ rows: [1] }), { status: 200 })));
    const init = { method: "POST", body: JSON.stringify({ country: "KR" }) };
    expect(peekCachedJson("/api/recommend", init)).toBeNull();
    await cachedFetch("/api/recommend", init, 0);
    expect(peekCachedJson("/api/recommend", init, 1)).toEqual({ rows: [1] });
    expect(peekCachedJson("/api/recommend", { ...init, body: JSON.stringify({ country: "US" }) }, 1)).toBeNull();
    expect(peekCachedJson("/api/recommend", init, 60 * 60_000)).toBeNull();
  });

  it("관심 목록도 화면 캐시를 쓴다 — 추천·종목 상세에 들어올 때마다 다시 읽었다", async () => {
    const 부름 = vi.fn(async () => new Response(JSON.stringify({ watchlist: [{ id: 9, stock_id: 3 }] }), { status: 200 }));
    vi.stubGlobal("fetch", 부름);
    expect((await loadWatchIds())?.get(3)).toBe(9);
    await loadWatchIds();
    expect(부름).toHaveBeenCalledTimes(1);
  });

  it("추천·적립 화면이 처음 그릴 때 캐시에서 채운다", () => {
    for (const f of [
      "components/RecommendList.tsx",
      "components/EtfList.tsx",
      "components/AccumulationStocks.tsx",
      "components/SatelliteList.tsx",
    ]) {
      const src = readFileSync(f, "utf-8");
      expect(src, f).toMatch(/peekCachedJson(<\w+>)?\(/);
      // 캐시가 있으면 "불러오는 중" 으로 시작하지 않는다
      expect(src, f).toMatch(/useState\((?:d0 === null|d0 === null\))/);
    }
  });

  it("메뉴로 옮겨 온 적립 화면은 저장된 나라·보기로 처음부터 그린다 — 국내·핵심을 한 번 더 읽지 않는다", () => {
    const src = readFileSync("components/EtfList.tsx", "utf-8");
    expect(src).toMatch(/useState<Country>\(\(\) => \(afterHydration\(\) \? readSavedCountry\(\) : "KR"\)\)/);
    expect(src).toMatch(/useState<View>\(\(\) => \(afterHydration\(\) \? readSavedView\(\) : "focus"\)\)/);
  });
});
