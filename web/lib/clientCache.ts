/**
 * 화면이 한 번 읽은 API 응답을 **브라우저 메모리에 잠시 둔다** (docs/infra.md 25.879, 2026-10-02 사용자 요청:
 * "한번 조회하면 페이지를 옮겼다 돌아와도 디비를 다시 읽지말고 기존 데이터를 보여주는게 좋지않을까?").
 *
 * 예전에는 메뉴를 오갈 때마다 화면이 다시 붙으며(mount) API → DB 를 새로 읽었다 — 추천·ETF·포트폴리오를 오가기만 해도 같은 질의를 되풀이했다.
 * 데이터 대부분은 하루 한 번 배치가 바꾸므로 잠시 두어도 낡지 않는다.
 *
 * - **성공한 응답만** 둔다. 실패는 다음에 다시 읽는다
 * - 열쇠는 (방법, 주소, 본문) — 국내·미국 탭, 스크리너 조건이 서로 섞이지 않는다
 * - **쓰기가 하나라도 있으면 전부 버린다**(`readJson` 의 POST·PUT·PATCH·DELETE 가 부른다). 매매를 넣고 포트폴리오로 가면 새로 읽는다
 * - 브라우저를 새로고침(F5)하면 메모리가 비어 새로 읽는다. 오래 열어 둔 탭은 `CLIENT_CACHE_MS` 뒤 새로 읽는다
 * - 상태 화면·백테스트 진행처럼 **새 값을 기다리는** 곳에는 쓰지 않는다
 */
export const CLIENT_CACHE_MS = 10 * 60_000;

interface Entry {
  at: number;
  status: number;
  text: string;
}

const 저장 = new Map<string, Entry>();

export function cacheKey(url: string, init?: RequestInit): string {
  const method = (init?.method ?? "GET").toUpperCase();
  const body = typeof init?.body === "string" ? init.body : "";
  return `${method} ${url} ${body}`;
}

export function clearClientCache(): void {
  저장.clear();
}

/** `fetch` 와 같은 모양 — 부르는 쪽 코드(`response.ok`·`response.json()`)를 그대로 둔다 */
export async function cachedFetch(url: string, init?: RequestInit, now: number = Date.now()): Promise<Response> {
  const key = cacheKey(url, init);
  const 있던 = 저장.get(key);
  if (있던 && now - 있던.at < CLIENT_CACHE_MS) {
    return new Response(있던.text, { status: 있던.status, headers: { "Content-Type": "application/json" } });
  }
  const response = await fetch(url, init);
  if (!response.ok) return response;
  const text = await response.text();
  저장.set(key, { at: now, status: response.status, text });
  return new Response(text, { status: response.status, headers: { "Content-Type": "application/json" } });
}

/**
 * **기다리지 않고** 지금 있는 응답을 꺼낸다 (docs/infra.md 25.893). 없거나 지났으면 null.
 *
 * 25.879 로 같은 응답을 다시 읽지 않게 했지만, 화면은 붙을 때마다 "불러오는 중…" 을 먼저 그리고 다음 틱에 캐시 값을
 * 그렸다 — 사용자에게는 "다시 조회한다" 로 보였다(2026-10-02 사용자 지적). 화면이 처음 그릴 때 이것으로 바로 채운다.
 * 서버 렌더·첫 하이드레이션에서는 이 모듈의 메모리가 비어 있어 늘 null 이다 — 서버와 첫 그림이 어긋나지 않는다.
 */
export function peekCachedJson<T = unknown>(url: string, init?: RequestInit, now: number = Date.now()): T | null {
  const 있던 = 저장.get(cacheKey(url, init));
  if (!있던 || now - 있던.at >= CLIENT_CACHE_MS) return null;
  try {
    return JSON.parse(있던.text) as T;
  } catch {
    return null;
  }
}

/**
 * 첫 하이드레이션이 끝났나 (25.893). 끝난 뒤에 붙는 화면(메뉴로 옮겨 온 화면)은 브라우저 저장소(국내·미국, 탭)를
 * **처음 그릴 때 바로** 읽어도 서버 그림과 어긋날 일이 없다. 예전에는 늘 "국내·핵심" 으로 그렸다가 저장된 "미국·종목" 으로
 * 바꾸느라 **필요 없는 첫 조회**를 한 번 더 했다.
 */
let 붙음 = false;

export function markHydrated(): void {
  붙음 = true;
}

export function afterHydration(): boolean {
  return 붙음 && typeof window !== "undefined";
}
