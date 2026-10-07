import { NextResponse, type NextRequest } from "next/server";
import { cookieOptions } from "@/lib/auth";

/**
 * 로그아웃 — 쿠키를 지우고 **로그인 화면으로 보낸다** (docs/infra.md 25.651, 감사).
 *
 * 버튼은 네이티브 폼 POST 인데 예전 응답은 `{"ok":true}` JSON 이라 브라우저가 그 글자를 한 화면으로 보여 줬다 —
 * 설치형 앱(주소창 없음)에서는 빠져나갈 길이 없었다. 303 은 POST 뒤 GET 으로 옮기라는 뜻이다.
 * 세션이 이미 만료됐어도 쿠키를 지울 수 있게 공개 경로다(proxy.ts) — 쿠키를 지우기만 하고 데이터가 없다.
 */
export async function POST(request: NextRequest) {
  const response = NextResponse.redirect(new URL("/login", request.url), 303);
  response.cookies.set({ ...cookieOptions, value: "", maxAge: 0 });
  return response;
}
