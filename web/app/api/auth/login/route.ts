import { NextResponse } from "next/server";
import {
  DEVICE_COOKIE,
  checkPassword,
  cookieOptions,
  createDeviceToken,
  createSessionToken,
  deviceCookieOptions,
  newDeviceId,
  verifyDeviceToken,
} from "@/lib/auth";
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
/** 같은 이름의 쿠키 값 전부 — 형제 도메인이 같은 이름을 심어 진짜 표를 가려도 맞는 것을 찾게 (25.1425, 교차검증) */
function cookieValues(request: Request, name: string): string[] {
  const out: string[] = [];
  for (const part of (request.headers.get("cookie") ?? "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === name) out.push(part.slice(i + 1).trim());
  }
  return out;
}

/** 맞는 기기 표의 기기 번호. 못 읽으면(서명키 없음 등) null — 예전처럼 주소로 센다(더 엄격한 쪽) */
async function trustedDeviceId(request: Request): Promise<string | null> {
  for (const value of cookieValues(request, DEVICE_COOKIE)) {
    try {
      const id = await verifyDeviceToken(value);
      if (id) return id;
    } catch {
      return null;
    }
  }
  return null;
}

export async function POST(request: Request) {
  // **전에 들어온 기기면 전체 잠금을 보지 않고 기기 번호로 센다** (docs/infra.md 25.1424, 보안 감사 #1) — 남이 전체 잠금을 계속 걸어
  // 본인 로그인을 막지 못하게. 기기 표를 못 읽으면(AUTH_SECRET 없음 등) 예전처럼 주소로
  const deviceId = await trustedDeviceId(request);
  const ipHash = await hashIp(deviceId ? `device:${deviceId}` : clientIp(request));

  // 먼저 실패로 적어 두고 센다 — 동시 요청이 서로를 보게 (docs/infra.md 25.393)
  const verdict = await reserveAttempt(ipHash, { trustedDevice: deviceId !== null });
  // 기기 표 요청이 전체 문턱을 넘긴 것을 봤으면 그때도 알린다 (25.1425, 교차검증) — 잠그지는 않지만 누군가 두드리는 중이다
  if (verdict.globalExceeded) await alertGlobalLock(new Date(), verdict.memory === true);
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
  // 기기 표를 (다시) 준다 — 있던 번호는 그대로, 만료만 늘린다. 못 만들면 세션은 그대로 준다(기기 표는 덧붙임)
  try {
    response.cookies.set({ ...deviceCookieOptions, value: await createDeviceToken(deviceId ?? newDeviceId()) });
  } catch {
    // 세션을 만든 같은 키라 여기서 실패할 일은 거의 없다
  }
  return response;
}
