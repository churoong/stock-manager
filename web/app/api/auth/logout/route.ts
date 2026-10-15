import { NextResponse, type NextRequest } from "next/server";
import { cookieOptions, deviceCookieOptions } from "@/lib/auth";

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
  // **기기 표도 지운다** (docs/infra.md 25.1425, 교차검증) — 공용 PC 에서 로그아웃한 뒤 그 브라우저를 쓰는 사람이 세션 없이도 전체
  // 잠금을 건너뛰는 기기 경로(15분 5번)로 알림 없이 맞혀 볼 수 있었다. 대가: 로그아웃한 기기는 다음 로그인 전까지 우회를 잃는다
  response.cookies.set({ ...deviceCookieOptions, value: "", maxAge: 0 });
  return response;
}
