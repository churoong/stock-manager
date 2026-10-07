import { readdirSync, readFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { SESSION_COOKIE, createSessionToken } from "@/lib/auth";
import { proxy } from "@/proxy";

/**
 * 문지기 테스트 (web/proxy.ts).
 *
 * **왜 이제서야 생겼나.** CLAUDE.md 의 절대 규칙 중 하나가 "로그인 없이는 어떤
 * 데이터도 반환하지 않는다" 다. 한국거래소 약관의 제3자 제공 금지가 걸린 자리라
 * 깨지면 데이터가 새는 것으로 끝나지 않는다. 그런데 그 규칙을 실제로 지키는
 * 코드는 `web/proxy.ts` **한 파일뿐**이고(각 API 경로는 "미들웨어가 앞에서
 * 막는다" 고 주석에 적고 스스로 검사하지 않는다), 2026-09-20 까지 그 파일의
 * 함수는 **테스트가 한 번도 실행한 적이 없었다.** 다른 테스트 두 곳이 파일을
 * 글자로 읽어 특정 문자열이 들어 있는지만 봤을 뿐이다.
 *
 * 여기서 지키는 것:
 *
 * 1. **경로를 하나씩 적지 않는다.** `app/api` 를 훑어 실제로 존재하는 모든 경로에
 *    대해 검사한다. 새 API 를 만들면 이 테스트가 자동으로 그것까지 본다 —
 *    목록을 손으로 늘리는 테스트는 늘리는 것을 잊는 순간 거짓말이 된다
 * 2. **크론 경로는 예외지만, 예외인 대신 스스로를 지켜야 한다.** `/api/cron/` 은
 *    문지기를 통과하므로 각 경로가 토큰을 직접 검사하는지 소스에서 확인한다
 * 3. **matcher 가 API 를 덮는가.** proxy() 가 아무리 옳아도 matcher 밖이면
 *    실행조차 되지 않는다. 문지기가 "없는" 상태와 구별되지 않는다
 * 4. **화면은 로그인으로 보내고 API 는 401 을 준다.** API 가 302 를 받으면
 *    폰 화면이 로그인 HTML 을 JSON 으로 읽고 엉뚱한 오류를 띄운다
 */

const SECRET = "테스트용-서명키-0123456789abcdef";
const 웹루트 = process.cwd();

beforeEach(() => {
  process.env.AUTH_SECRET = SECRET;
});

/** `app/api` 아래 실제로 있는 경로들. 손으로 적은 목록이 아니다. */
function api경로들(): string[] {
  const 뿌리 = join(웹루트, "app", "api");
  return readdirSync(뿌리, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name === "route.ts")
    .map((entry) => {
      const 디렉터리 = relative(뿌리, entry.parentPath ?? (entry as { path: string }).path);
      return `/api/${디렉터리.split(sep).join("/")}`.replace(/\/$/, "");
    })
    .sort();
}

/** 동적 구간([id])은 실제 주소 모양으로 바꾼다. 문지기는 값을 보지 않는다. */
function 실제주소(경로: string): string {
  return 경로.replace(/\[\.\.\.(\w+)\]/g, "값1/값2").replace(/\[(\w+)\]/g, "값");
}

function 요청(경로: string, 쿠키?: string): NextRequest {
  const headers: Record<string, string> = {};
  if (쿠키) headers.cookie = `${SESSION_COOKIE}=${쿠키}`;
  return new NextRequest(`https://example.com${경로}`, { headers });
}

/** 로그인·로그아웃 처리는 로그인 없이 불러야 한다(로그아웃은 쿠키를 지우기만 한다, 25.651). 아래에서 따로 본다 */
const 공개API = ["/api/auth/login", "/api/auth/logout"];

const 데이터경로들 = api경로들().filter(
  (p) => !p.startsWith("/api/cron/") && !공개API.includes(p),
);
const 크론경로들 = api경로들().filter((p) => p.startsWith("/api/cron/"));

describe("경로를 세어 둔다", () => {
  it("데이터 API 가 실제로 있다", () => {
    // 훑기가 조용히 0개를 내면 아래 테스트가 전부 통과한다 — 그것이 가장 위험하다
    expect(데이터경로들.length).toBeGreaterThan(10);
    expect(크론경로들.length).toBeGreaterThan(0);
  });
});

describe("로그인 없이는 어떤 데이터도 돌려주지 않는다", () => {
  it.each(데이터경로들)("%s 는 쿠키가 없으면 401", async (경로) => {
    const response = await proxy(요청(실제주소(경로)));

    expect(response.status).toBe(401);
    // 401 이어도 본문에 데이터가 실리면 안 된다
    expect(await response.json()).toEqual({ error: "로그인이 필요합니다" });
  });

  it.each(데이터경로들)("%s 는 위조 쿠키도 401", async (경로) => {
    // 쿠키는 헤더라 ASCII 만 담긴다. 서명 자리에 아무 base64url 이나 넣는다
    const 위조 = `${btoa(JSON.stringify({ exp: Date.now() + 9_999_999 }))}.ZmFrZXNpZ25hdHVyZQ`;
    const response = await proxy(요청(실제주소(경로), 위조));

    expect(response.status).toBe(401);
  });

  it("만료된 쿠키는 401", async () => {
    const 만료됨 = await createSessionToken(Date.now() - 31 * 24 * 60 * 60 * 1000 - 1000);
    const response = await proxy(요청("/api/portfolio", 만료됨));

    expect(response.status).toBe(401);
  });

  it("서명이 맞고 만료 전이면 통과시킨다", async () => {
    const response = await proxy(요청("/api/portfolio", await createSessionToken()));

    // NextResponse.next() 는 200 이고 본문을 만들지 않는다
    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("다른 서명키로 만든 쿠키는 401", async () => {
    process.env.AUTH_SECRET = "남의-서명키-0123456789abcdef";
    const 남의토큰 = await createSessionToken();
    process.env.AUTH_SECRET = SECRET;

    expect((await proxy(요청("/api/portfolio", 남의토큰))).status).toBe(401);
  });
});

describe("화면은 로그인으로 보낸다", () => {
  it.each(["/", "/portfolio", "/settings", "/stocks/005930"])(
    "%s 는 쿠키가 없으면 /login 으로 보낸다",
    async (경로) => {
      const response = await proxy(요청(경로));

      expect(response.status).toBe(307);
      expect(new URL(response.headers.get("location") ?? "").pathname).toBe("/login");
    },
  );

  it("API 는 화면으로 보내지 않는다", async () => {
    // 폰이 로그인 HTML 을 JSON 으로 읽으면 "데이터가 없다" 로 보인다
    const response = await proxy(요청("/api/portfolio"));

    expect(response.headers.get("location")).toBeNull();
  });

  it("로그인 화면은 쿠키 없이 열린다", async () => {
    expect((await proxy(요청("/login"))).headers.get("x-middleware-next")).toBe("1");
  });

  it("로그인 처리 경로는 쿠키 없이 열린다", async () => {
    expect((await proxy(요청("/api/auth/login"))).headers.get("x-middleware-next")).toBe("1");
  });

  it("이미 들어와 있으면 로그인 화면에서 앱으로 보낸다", async () => {
    const response = await proxy(요청("/login", await createSessionToken()));

    expect(new URL(response.headers.get("location") ?? "").pathname).toBe("/settings");
  });
});

describe("예외 경로", () => {
  it.each(크론경로들)("%s 는 문지기를 통과한다", async (경로) => {
    expect((await proxy(요청(경로))).headers.get("x-middleware-next")).toBe("1");
  });

  it.each(크론경로들)("%s 는 대신 토큰을 스스로 검사한다", (경로) => {
    // 통과시키는 대가다. 이 검사가 빠진 크론 경로는 누구나 부를 수 있다
    const 소스 = readFileSync(join(웹루트, "app", 경로.slice(1), "route.ts"), "utf-8");

    expect(소스).toContain("tokenMatches");
    expect(소스).toContain("CRON_SECRET");
  });

  it("설치용 정적 파일만 통과한다", async () => {
    for (const 파일 of ["/manifest.webmanifest", "/sw.js", "/offline.html"]) {
      expect((await proxy(요청(파일))).headers.get("x-middleware-next")).toBe("1");
    }
    // 이름이 비슷한 다른 경로까지 열리면 안 된다
    expect((await proxy(요청("/sw.js.map"))).status).toBe(307);
    expect((await proxy(요청("/api/cron"))).status).toBe(401);
  });

  it("robots.txt 는 로그인 없이 읽힌다 — 크롤러는 쿠키가 없다 (25.199)", async () => {
    // 로그인 뒤에 두면 크롤러가 받는 것은 로그인 화면으로 가는 307 이다. "세 겹" 의 한 겹이 사라진다
    expect((await proxy(요청("/robots.txt"))).headers.get("x-middleware-next")).toBe("1");
    // 그 파일은 막는 말뿐이어야 한다 — 열어 둔 대가다
    const { default: robots } = await import("@/app/robots");
    expect(robots()).toEqual({ rules: { userAgent: "*", disallow: "/" } });
  });

  it("공개 경로는 여섯 가지뿐이다", async () => {
    const 소스 = readFileSync(join(웹루트, "proxy.ts"), "utf-8");
    const 목록 = 소스.match(/const PUBLIC_PATHS = \[(.*?)\]/s)?.[1] ?? "";

    // 늘어나면 왜 늘렸는지 생각하고 이 숫자를 고치라는 뜻이다
    expect(목록.match(/"/g)?.length).toBe(6); // 로그인 화면·로그인·로그아웃 (25.651)
  });
});

describe("matcher 가 실제로 이 경로들을 덮는가", () => {
  /**
   * **소스에서 뽑아 쓴다.** 여기에 같은 정규식을 다시 적으면, 정작 proxy.ts 의
   * matcher 를 좁혀도 이 테스트는 자기가 적은 것만 보고 통과한다.
   */
  const matcher = (() => {
    const 소스 = readFileSync(join(웹루트, "proxy.ts"), "utf-8");
    const 적힌것 = 소스.match(/matcher:\s*\[\s*"([^"]+)"/)?.[1];
    expect(적힌것, "proxy.ts 에서 matcher 를 찾지 못했다").toBeTruthy();
    return new RegExp(`^${적힌것}$`);
  })();

  it.each(데이터경로들)("%s 는 matcher 안에 있다", (경로) => {
    // 밖이면 proxy() 가 아무리 옳아도 실행되지 않는다 — 문지기가 없는 것과 같다
    expect(matcher.test(실제주소(경로))).toBe(true);
  });

  it.each(["/", "/portfolio", "/login", ...공개API, ...크론경로들])(
    "%s 도 matcher 안에 있다",
    (경로) => {
      expect(matcher.test(경로)).toBe(true);
    },
  );
});

describe("공개 경로는 정확히 같을 때만, 상태를 바꾸는 API 는 같은 출처만 (docs/infra.md 25.626)", () => {
  // 서명 키에 섞이는 값이라 끝나면 되돌린다 — 뒤에 붙는 테스트가 물려받지 않게 (25.628, 교차검증)
  const 예전 = { AUTH_SECRET: process.env.AUTH_SECRET, APP_PASSWORD: process.env.APP_PASSWORD };
  beforeEach(() => {
    process.env.AUTH_SECRET = "테스트-비밀-값-충분히-길게-0123456789";
    process.env.APP_PASSWORD = "테스트비번";
  });
  afterEach(() => {
    for (const [k, v] of Object.entries(예전)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  });

  it("공개 경로의 하위 경로는 쿠키 없이 열리지 않는다", async () => {
    expect((await proxy(new NextRequest("https://example.com/login/anything"))).status).toBe(307);
    expect((await proxy(new NextRequest("https://example.com/api/auth/login/x"))).status).toBe(401);
  });

  it("다른 출처의 POST 는 쿠키가 있어도 403", async () => {
    const 쿠키 = `${SESSION_COOKIE}=${await createSessionToken()}`;
    const 남 = new NextRequest("https://example.com/api/trades", {
      method: "POST",
      headers: { origin: "https://evil.example.net", cookie: 쿠키 },
    });
    expect((await proxy(남)).status).toBe(403);
    const 우리 = new NextRequest("https://example.com/api/trades", {
      method: "POST",
      headers: { origin: "https://example.com", cookie: 쿠키 },
    });
    expect((await proxy(우리)).headers.get("x-middleware-next")).toBe("1");
  });

  it("Origin 이 없으면 Sec-Fetch-Site 로 본다", async () => {
    const 쿠키 = `${SESSION_COOKIE}=${await createSessionToken()}`;
    const 남 = new NextRequest("https://example.com/api/settings", {
      method: "PUT",
      headers: { "sec-fetch-site": "cross-site", cookie: 쿠키 },
    });
    expect((await proxy(남)).status).toBe(403);
  });
});

describe("출처 비교는 실제로 들어온 호스트와도 맞춘다 (25.626)", () => {
  it("nextUrl 과 달라도 x-forwarded-host 가 같으면 받는다", async () => {
    const { crossSiteOk } = await import("@/proxy");
    const 요청_ = new NextRequest("http://internal:3000/api/trades", {
      method: "POST",
      headers: { origin: "https://my-app.vercel.app", "x-forwarded-host": "my-app.vercel.app" },
    });
    expect(crossSiteOk(요청_)).toBe(true);
  });
});


describe("Origin: null 은 막지 않는다 — no-referrer 정책의 같은 출처 POST (docs/infra.md 25.628, 교차검증)", () => {
  it("로그인 POST 가 Origin null + same-origin 이면 통과한다", async () => {
    const 요청_ = new NextRequest("https://example.com/api/auth/login", {
      method: "POST",
      headers: { origin: "null", "sec-fetch-site": "same-origin" },
    });
    expect((await proxy(요청_)).status).not.toBe(403);
  });

  it("Origin null 이어도 Sec-Fetch-Site 가 cross-site 면 막는다", async () => {
    const { crossSiteOk } = await import("@/proxy");
    const 요청_ = new NextRequest("https://example.com/api/trades", {
      method: "POST",
      headers: { origin: "null", "sec-fetch-site": "cross-site" },
    });
    expect(crossSiteOk(요청_)).toBe(false);
  });
});
