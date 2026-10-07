import { cachedFetch, clearClientCache } from "@/lib/clientCache";

/**
 * 화면이 API 를 읽을 때 쓰는 공통 도우미.
 *
 * **절대 던지지 않는다. 실패는 값으로 돌려준다.**
 *
 * 왜 있나 (2026-09-17 사용자 보고로 드러났다): 포트폴리오 화면이 네 경로를 한 try 안에서
 * 파싱하고 있었다. 배포 직후 새 경로가 아직 없으면 오류 페이지(HTML)가 오고 `json()` 이
 * 터진다. 그러면 그 뒤의 setState 가 **하나도** 실행되지 않아 매매 기록과 보유가 통째로
 * 비어 보였다. 한 화면이 여러 경로에 걸려 있으면 가장 약한 하나가 전체를 끈다.
 *
 * 그래서 읽기는 여기 한 곳에서만 하고, 화면은 성공한 것부터 그린다.
 */

export interface Read<T = unknown> {
  ok: boolean;
  data: T | null;
  /** 사람이 읽는 실패 이유. 성공이면 null */
  error: string | null;
  status: number;
}

export async function readJson<T = unknown>(
  url: string,
  init?: RequestInit,
  opts: { cache?: boolean } = {},
): Promise<Read<T>> {
  // 쓰기면 화면 캐시를 모두 버린다 — 다음 화면이 새 값을 읽게 (25.879)
  if (init?.method && init.method.toUpperCase() !== "GET") clearClientCache();
  try {
    const response = opts.cache ? await cachedFetch(url, init) : await fetch(url, init);
    const text = await response.text();

    let data: unknown = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        // 로그인 화면이나 오류 페이지가 HTML 로 온 경우다. 본문을 그대로 보여 주지 않는다
        return {
          ok: false,
          data: null,
          error: response.status === 401 ? "로그인이 필요합니다" : `응답을 읽지 못했습니다 (HTTP ${response.status})`,
          status: response.status,
        };
      }
    }

    if (!response.ok) {
      const body = data as { errors?: string[]; error?: string } | null;
      return {
        ok: false,
        data: data as T,
        error: body?.errors?.join(", ") ?? body?.error ?? `HTTP ${response.status}`,
        status: response.status,
      };
    }
    return { ok: true, data: data as T, error: null, status: response.status };
  } catch (e) {
    // 네트워크가 끊긴 경우. 오프라인에서도 화면이 죽지 않아야 한다
    return { ok: false, data: null, error: e instanceof Error ? e.message : "불러오지 못했습니다", status: 0 };
  }
}

/**
 * 쓰기·검색 경로용: `readJson` 결과에서 **본문 하나**를 꺼낸다 (docs/infra.md 25.281).
 * 본문을 못 읽었으면 `{ errors: [사유] }` 를 돌려주므로 부르는 쪽의 `j.errors?.join(...)` 이 그대로 돈다.
 * 예전에는 매매·배당·관심 저장과 종목 검색이 `await r.json()` 을 직접 불러, 배포 직후 HTML 오류 응답이나
 * 네트워크 끊김이면 예외로 끝나 **"저장 중…" 에 멈추고** 검색은 조용히 죽었다.
 */
export function bodyOf<T extends object = Record<string, unknown>>(r: Read<unknown>): T & { errors?: string[] } {
  if (r.data && typeof r.data === "object") return r.data as T & { errors?: string[] };
  return { errors: [r.error ?? "응답을 읽지 못했습니다"] } as unknown as T & { errors?: string[] };
}
