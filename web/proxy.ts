/**
 * 모든 경로를 인증 뒤에 둔다.
 *
 * 한국거래소 약관이 데이터의 제3자 제공을 금지한다. 로그인하지 않은 요청에는
 * 어떤 데이터도 돌려주지 않는다. 화면뿐 아니라 API 경로도 마찬가지다.
 *
 * 예외는 여섯뿐이다.
 *   로그인 화면, 로그인 처리, 외부 크론이 부르는 경로, 앱 설치용 정적 파일, `robots.txt`, 그리고 텔레그램 웹훅 하나.
 * 크론 경로는 세션 대신 비밀 토큰으로 스스로를 지킨다. 텔레그램 웹훅도 비밀 머리글과 본인 대화방 번호로 지킨다
 * (docs/infra.md 25.1004) — 정확히 그 경로만 연다(`webhook-setup` 은 로그인 뒤다).
 *
 * `robots.txt` 는 "들어오지 마라" 는 한 줄이라 데이터가 없다. 2026-09-26 까지 이것도 로그인 뒤에
 * 있어서 검색엔진이 받는 것은 **로그인 화면으로 보내는 307** 이었다 — "세 겹으로 막는다" 의 한 겹이
 * 처음부터 없었다(docs/infra.md 25.199).
 *
 * 설치용 파일(매니페스트·아이콘·서비스 워커·오프라인 안내)에는 **데이터가 없다.**
 * 이것까지 로그인 뒤에 두면 홈 화면 설치가 실패한다 — 브라우저가 매니페스트를
 * 읽을 때 쿠키 없이 요청하는 경우가 있어서다 (docs/pwa.md).
 */
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, verifySessionToken } from "@/lib/auth";

// 로그아웃은 쿠키를 지우기만 한다 — 만료된 세션에서도 눌러서 지워져야 한다 (25.651)
const PUBLIC_PATHS = ["/login", "/api/auth/login", "/api/auth/logout"];
const CRON_PREFIX = "/api/cron/";
/** 텔레그램이 부르는 질의응답 웹훅 (25.1004). 경로가 스스로 비밀 머리글·대화방을 확인한다 */
export const TELEGRAM_WEBHOOK = "/api/telegram/webhook";

/** 앱 설치에 필요한 정적 파일. 데이터가 들어 있지 않다 (docs/pwa.md 3장) */
const PWA_FILES = new Set([
  "/manifest.webmanifest",
  "/sw.js",
  "/offline.html",
  "/icon-192.png",
  "/icon-512.png",
  "/icon-maskable-512.png",
  "/apple-touch-icon.png",
  // 색인 차단 파일(app/robots.ts). 로그인 뒤에 두면 크롤러가 이것을 못 읽는다 (25.199)
  "/robots.txt",
]);

/** 같은 출처에서 온 요청인가 (25.626). 판단할 머리글이 없으면 참 — 쿠키 확인이 그대로 막는다 */
export function crossSiteOk(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  // **`Origin: null` 은 "모른다" 로 본다** (docs/infra.md 25.628, 교차검증 — 25.626 의 상급 퇴행). 이 앱은 모든 경로에
  // `Referrer-Policy: no-referrer`(next.config.ts)를 붙이는데, 그 정책이면 브라우저가 **같은 출처 POST 에도 `Origin: null`**
  // 을 보낸다(Fetch 표준 "append a request Origin header"). 25.626 은 그것을 다른 출처로 보고 로그인·로그아웃·모든 저장을
  // 403 으로 막을 수 있었다. null 이면 `Sec-Fetch-Site` 로 넘긴다
  if (origin && origin !== "null") {
    // 호스트로 견준다 — 배포 뒤에서 `nextUrl.origin` 이 브라우저의 Origin 과 다르게 잡혀도(프록시·스킴) 모든 저장이
    // 403 이 되지 않게, 요청이 실제로 들어온 호스트(x-forwarded-host → host)와도 맞춰 본다 [확인필요: Vercel 머리글]
    let 온곳: string;
    try {
      온곳 = new URL(origin).host;
    } catch {
      return false;
    }
    const 우리 = [request.nextUrl.host, request.headers.get("x-forwarded-host"), request.headers.get("host")];
    return 우리.some((h) => h !== null && h !== "" && h === 온곳);
  }
  const site = request.headers.get("sec-fetch-site");
  if (site) return site === "same-origin" || site === "none";
  return true;
}

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (pathname.startsWith(CRON_PREFIX) || PWA_FILES.has(pathname) || pathname === TELEGRAM_WEBHOOK) {
    return NextResponse.next();
  }

  // **정확히 같은 경로만** 공개다 (docs/infra.md 25.626, 감사). 예전에는 접두사로 맞춰 `/login/…`·`/api/auth/login/…` 아래
  // 무엇이든 쿠키 없이 통과했다 — 지금은 그런 경로가 없지만, 하나 생기면 바로 공개가 된다
  const isPublic = PUBLIC_PATHS.includes(pathname);

  // **상태를 바꾸는 API 는 같은 출처에서 온 것만 받는다** (25.626, 감사). CSRF 방어가 쿠키 SameSite=Lax 하나뿐이었다 —
  // 같은 사이트(사용자 도메인의 하위 도메인)에 다른 서비스가 올라가거나 쿠키 설정이 바뀌면 바로 뚫린다.
  // Origin 이 있으면 우리 출처와 같아야 하고, 없으면 Sec-Fetch-Site 로 본다. 둘 다 없는 요청(스크립트)은 쿠키가 있어야 하므로 그대로 둔다
  if (pathname.startsWith("/api/") && !["GET", "HEAD", "OPTIONS"].includes(request.method) && !crossSiteOk(request)) {
    return NextResponse.json({ error: "다른 출처에서 온 요청은 받지 않습니다" }, { status: 403 });
  }

  const token = request.cookies.get(SESSION_COOKIE)?.value;
  const signedIn = await verifySessionToken(token);

  if (isPublic) {
    if (signedIn && pathname === "/login") {
      return NextResponse.redirect(new URL("/settings", request.url));
    }
    return NextResponse.next();
  }

  if (signedIn) {
    return NextResponse.next();
  }

  // API 는 화면으로 보내지 않는다. 401 을 그대로 준다.
  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "로그인이 필요합니다" }, { status: 401 });
  }

  const target = new URL("/login", request.url);
  return NextResponse.redirect(target);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
