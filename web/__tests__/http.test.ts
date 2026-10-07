import { afterEach, describe, expect, it } from "vitest";
import { readJson } from "@/lib/http";

/**
 * 화면이 API 를 읽는 공통 도우미 (`web/lib/http.ts`).
 *
 * **왜 이제서야 생겼나.** 2026-09-20 에 웹 덮임을 처음 재 보니 이 파일이 **0%** 였다.
 * 한 줄도 돌아 본 적이 없다. 그런데 이 파일은 **실제로 겪은 사고를 막으려고** 만든
 * 것이다(2026-09-17): 포트폴리오 화면이 네 경로를 한 `try` 안에서 파싱하다가, 배포
 * 직후 아직 없는 경로가 HTML 오류 페이지를 주는 바람에 `json()` 이 터졌고 **그 뒤의
 * setState 가 하나도 실행되지 않아 매매 기록과 보유가 통째로 비어 보였다.**
 *
 * 그 사고를 막는 장치가 검증 없이 넉 달을 돌 뻔했다. 여기서 지키는 약속은 하나다 —
 * **절대 던지지 않는다. 실패는 값으로 돌려준다.**
 *
 * 그리고 값의 모양이 약속대로여야 한다:
 *
 * - `ok` 가 거짓이면 **사람이 읽을 수 있는 `error`** 가 있다. 화면이 그대로 띄운다
 * - 본문이 HTML 이어도 **그 본문을 화면에 흘리지 않는다**. 로그인 페이지의 HTML 을
 *   오류 메시지로 띄우면 무슨 일인지 알 수 없다
 * - 401 은 "로그인이 필요합니다" 다. 그 말을 보고 사람이 할 일을 안다
 * - 네트워크가 끊기면 `status` 가 0 이다. HTTP 오류와 구별된다
 */

const 원래fetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = 원래fetch;
});

/** 정해진 응답을 돌려준다. 예외를 주면 네트워크가 끊긴 것으로 흉내 낸다 */
function 응답(본문: string, init: ResponseInit | Error): string[] {
  const 부른것: string[] = [];
  globalThis.fetch = (async (url: string) => {
    부른것.push(String(url));
    if (init instanceof Error) throw init;
    return new Response(본문, init);
  }) as typeof fetch;
  return 부른것;
}

describe("성공", () => {
  it("JSON 을 그대로 돌려준다", async () => {
    응답(JSON.stringify({ trades: [1, 2] }), { status: 200 });

    const r = await readJson<{ trades: number[] }>("/api/trades");

    expect(r).toEqual({ ok: true, data: { trades: [1, 2] }, error: null, status: 200 });
  });

  it("init 을 그대로 넘긴다", async () => {
    // DELETE·PATCH 도 이 함수를 쓴다 (ScreenerForm)
    let 받은init: RequestInit | undefined;
    globalThis.fetch = (async (_u: string, init: RequestInit) => {
      받은init = init;
      return new Response("{}", { status: 200 });
    }) as typeof fetch;

    await readJson("/api/screener/presets/1", { method: "DELETE" });

    expect(받은init?.method).toBe("DELETE");
  });

  it("본문이 비어 있으면 data 가 null 이다", async () => {
    // **부르는 쪽이 알아야 할 모양이다.** ok 가 참인데 data 가 null 이므로,
    // `(r.data as {...}).x` 처럼 바로 파고들면 거기서 던진다.
    // PortfolioView 가 이 자리에 `?.` 를 안 쓰고 있었다 (2026-09-21 고침)
    응답("", { status: 200 });

    const r = await readJson("/api/portfolio");

    expect(r.ok).toBe(true);
    expect(r.data).toBeNull();
  });
});

describe("실패해도 던지지 않는다", () => {
  it("HTML 이 오면 본문을 흘리지 않고 이유만 말한다", async () => {
    // 배포 직후 없는 경로가 오류 페이지를 준다. 이것이 2026-09-17 사고의 모양이다
    응답("<!doctype html><h1>500</h1>", { status: 500 });

    const r = await readJson("/api/review");

    expect(r.ok).toBe(false);
    expect(r.error).toContain("HTTP 500");
    expect(r.error).not.toContain("<");
    expect(r.data).toBeNull();
  });

  it("401 에 HTML 이 오면 로그인하라고 말한다", async () => {
    // 문지기는 API 에 JSON 401 을 주지만(proxy.ts), 화면 경로로 새면 HTML 이 온다
    응답("<html>로그인</html>", { status: 401 });

    expect((await readJson("/api/portfolio")).error).toBe("로그인이 필요합니다");
  });

  it("errors 배열을 이어 붙인다", async () => {
    응답(JSON.stringify({ errors: ["기간이 잘못됐습니다", "종목이 없습니다"] }), { status: 400 });

    const r = await readJson("/api/backtest");

    expect(r.error).toBe("기간이 잘못됐습니다, 종목이 없습니다");
  });

  it("error 하나만 와도 읽는다", async () => {
    응답(JSON.stringify({ error: "로그인이 필요합니다" }), { status: 401 });

    expect((await readJson("/api/trades")).error).toBe("로그인이 필요합니다");
  });

  it("실패해도 본문은 넘겨준다", async () => {
    // 화면이 더 자세한 것을 꺼내 쓸 수 있어야 한다 (ScreenerForm 이 data?.errors 를 본다)
    응답(JSON.stringify({ errors: ["안 됨"], detail: 42 }), { status: 422 });

    const r = await readJson<{ detail: number }>("/api/screener");

    expect(r.ok).toBe(false);
    expect(r.data).toEqual({ errors: ["안 됨"], detail: 42 });
  });

  it("아무 말도 없으면 상태 코드라도 말한다", async () => {
    응답(JSON.stringify({}), { status: 503 });

    expect((await readJson("/api/status")).error).toBe("HTTP 503");
  });

  it("네트워크가 끊기면 status 가 0 이다", async () => {
    // 오프라인이다. HTTP 오류와 구별돼야 화면이 "연결을 확인하세요" 를 띄울 수 있다
    응답("", new TypeError("Failed to fetch"));

    const r = await readJson("/api/portfolio");

    expect(r).toEqual({ ok: false, data: null, error: "Failed to fetch", status: 0 });
  });

  it("Error 가 아닌 것이 던져져도 말이 되는 문장을 준다", async () => {
    globalThis.fetch = (async () => {
      throw "이상한 것"; // eslint-disable-line no-throw-literal
    }) as typeof fetch;

    expect((await readJson("/api/portfolio")).error).toBe("불러오지 못했습니다");
  });
});

describe("한 경로가 무너져도 나머지는 산다", () => {
  it("Promise.all 로 묶어도 던지지 않는다", async () => {
    // 이것이 이 파일의 존재 이유다. 하나가 던지면 Promise.all 이 통째로 거부되고
    // 그 뒤의 setState 가 **하나도** 실행되지 않는다 (2026-09-17)
    let 차례 = 0;
    globalThis.fetch = (async () => {
      차례 += 1;
      if (차례 === 2) throw new TypeError("끊김");
      if (차례 === 3) return new Response("<html>", { status: 404 });
      return new Response(JSON.stringify({ n: 차례 }), { status: 200 });
    }) as typeof fetch;

    const 결과 = await Promise.all([
      readJson("/api/a"),
      readJson("/api/b"),
      readJson("/api/c"),
      readJson("/api/d"),
    ]);

    expect(결과.map((r) => r.ok)).toEqual([true, false, false, true]);
    expect(결과[0].data).toEqual({ n: 1 });
    expect(결과[3].data).toEqual({ n: 4 });
  });
});
