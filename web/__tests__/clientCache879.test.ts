/** 한 번 읽은 화면 데이터를 메뉴를 오가도 다시 읽지 않는다 (docs/infra.md 25.879, 2026-10-02 사용자 요청) */
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CLIENT_CACHE_MS, cachedFetch, clearClientCache } from "@/lib/clientCache";
import { readJson } from "@/lib/http";

function 세는fetch() {
  const 부름 = vi.fn(async (url: string, init?: RequestInit) =>
    new Response(JSON.stringify({ url, body: init?.body ?? null, n: 부름.mock.calls.length }), { status: 200 }),
  );
  vi.stubGlobal("fetch", 부름);
  return 부름;
}

describe("화면 캐시", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    clearClientCache();
  });

  it("같은 조회는 한 번만 부르고, 다른 탭(본문)은 따로 부른다", async () => {
    const 부름 = 세는fetch();
    const 국내 = { method: "POST", body: JSON.stringify({ country: "KR" }) };
    const a = await (await cachedFetch("/api/recommend", 국내, 0)).json();
    const b = await (await cachedFetch("/api/recommend", 국내, 1_000)).json();
    expect(b).toEqual(a);
    expect(부름).toHaveBeenCalledTimes(1);
    await cachedFetch("/api/recommend", { method: "POST", body: JSON.stringify({ country: "US" }) }, 2_000);
    expect(부름).toHaveBeenCalledTimes(2);
  });

  it("오래되면 다시 읽고, 실패는 두지 않는다", async () => {
    const 부름 = 세는fetch();
    await cachedFetch("/api/etf", undefined, 0);
    await cachedFetch("/api/etf", undefined, CLIENT_CACHE_MS + 1);
    expect(부름).toHaveBeenCalledTimes(2);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 500 })));
    await cachedFetch("/api/x", undefined, 0);
    const 다시 = 세는fetch();
    await cachedFetch("/api/x", undefined, 1);
    expect(다시).toHaveBeenCalledTimes(1);
  });

  it("쓰기가 하나라도 있으면 모두 버린다 — 매매를 넣고 포트폴리오로 가면 새로 읽는다", async () => {
    const 부름 = 세는fetch();
    await readJson("/api/portfolio", undefined, { cache: true });
    await readJson("/api/portfolio", undefined, { cache: true });
    expect(부름).toHaveBeenCalledTimes(1);
    await readJson("/api/trades", { method: "POST", body: "{}" });
    await readJson("/api/portfolio", undefined, { cache: true });
    expect(부름).toHaveBeenCalledTimes(3);
  });

  it("화면에서 배치를 돌리는 단추·새로고침 단추가 없다", () => {
    expect(readFileSync("components/StatusView.tsx", "utf-8")).not.toContain("/api/status/run");
    for (const f of ["components/RecommendList.tsx", "components/EtfList.tsx"]) {
      expect(readFileSync(f, "utf-8"), f).not.toMatch(/>\s*새로고침\s*</);
    }
  });
});
