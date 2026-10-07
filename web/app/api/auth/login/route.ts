import { NextResponse } from "next/server";
import { checkPassword, cookieOptions, createSessionToken } from "@/lib/auth";
import {
  alertGlobalLock,
  reserveAttempt,
  cleanupAttempts,
  clientIp,
  failureDelayMs,
  hashIp,
  recordAttempt,
} from "@/lib/loginGuard";

/**
 * 로그인.
 *
 * 비밀번호가 짧아도 되도록 시도 횟수를 막는다. 자세한 기준은 lib/loginGuard.ts.
 *
 * 실패해도 이유를 나누어 알려주지 않는다. 비밀번호 하나뿐이라 알려줄 것도 없고,
 * 알려주면 공격자에게만 도움이 된다. 잠겼을 때만 남은 시간을 알려준다.
 * 본인이 기다려야 할 시간을 모르면 곤란하기 때문이다.
 */
export async function POST(request: Request) {
  const ipHash = await hashIp(clientIp(request));

  // 먼저 실패로 적어 두고 센다 — 동시 요청이 서로를 보게 (docs/infra.md 25.393)
  const verdict = await reserveAttempt(ipHash);
  if (!verdict.allowed) {
    if (verdict.reason === "global") {
      await alertGlobalLock(new Date(), verdict.memory === true);
    }
    const minutes = Math.ceil(verdict.retryAfterSeconds / 60);
    return NextResponse.json(
      {
        error:
          verdict.reason === "global"
            ? `실패가 너무 많아 전체 잠금 상태입니다. ${minutes}분 뒤에 다시 시도하세요`
            : verdict.reason === "unavailable"
              ? "시도 기록을 확인할 수 없어 잠시 막았습니다. 잠시 뒤에 다시 시도하세요"
              : `시도가 너무 많습니다. ${minutes}분 뒤에 다시 시도하세요`,
        locked: true,
        retry_after_seconds: verdict.retryAfterSeconds,
      },
      { status: 429, headers: { "Retry-After": String(verdict.retryAfterSeconds) } },
    );
  }

  let password = "";
  try {
    const body = await request.json();
    password = typeof body?.password === "string" ? body.password : "";
  } catch {
    password = "";
  }

  if (!checkPassword(password)) {
    // 실패 줄은 이미 넣었다(reserveAttempt). 오래된 기록만 치운다
    await cleanupAttempts();
    // 실패가 쌓일수록 느려진다. 잠기기 전에도 공격자를 늦춘다.
    await new Promise((resolve) =>
      setTimeout(resolve, failureDelayMs(verdict.recentFailures)),
    );
    return NextResponse.json({ error: "비밀번호가 맞지 않습니다" }, { status: 401 });
  }

  await recordAttempt(ipHash, true);

  // 여기까지 왔으면 비밀번호는 맞았다. 이 뒤의 실패는 설정 문제다.
  // 빈 500 을 돌려주면 원인을 찾느라 시간을 버린다. 무엇이 없는지 말해 준다.
  // 비밀번호를 아는 사람만 이 응답을 볼 수 있으므로 알려도 안전하다.
  let token: string;
  try {
    token = await createSessionToken();
  } catch (error) {
    const message = error instanceof Error ? error.message : "세션을 만들지 못했습니다";
    return NextResponse.json(
      {
        error: `비밀번호는 맞았지만 세션을 만들지 못했습니다. ${message}`,
        hint: "배포 환경의 환경변수를 확인하세요. 값을 바꾼 뒤에는 재배포해야 반영됩니다",
      },
      { status: 500 },
    );
  }

  const response = NextResponse.json({ ok: true });
  response.cookies.set({ ...cookieOptions, value: token });
  return response;
}
