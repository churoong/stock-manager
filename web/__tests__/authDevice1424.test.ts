import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createDeviceToken,
  createSessionToken,
  newDeviceId,
  verifyDeviceToken,
  verifySessionToken,
} from "@/lib/auth";

const 부름 = vi.hoisted(() => ({
  열쇠: [] as string[],
  믿음: [] as boolean[],
  알림: 0,
  넘음: false,
}));
vi.mock("@/lib/loginGuard", () => ({
  clientIp: () => "1.2.3.4",
  hashIp: async (s: string) => {
    부름.열쇠.push(s);
    return `h:${s}`;
  },
  reserveAttempt: async (_h: string, o: { trustedDevice?: boolean } = {}) => {
    부름.믿음.push(o.trustedDevice === true);
    return {
      allowed: true,
      recentFailures: 0,
      ...(부름.넘음 ? { globalExceeded: true } : {}),
    };
  },
  cleanupAttempts: async () => {},
  recordAttempt: async () => {},
  failureDelayMs: () => 0,
  alertGlobalLock: async () => {
    부름.알림 += 1;
  },
}));

// 기기 표 (docs/infra.md 25.1424, 보안 감사 #1) — 세션으로 쓸 수 없고, 비밀번호를 바꾸면 무효
describe("기기 표", () => {
  beforeEach(() => {
    process.env.APP_PASSWORD = "0302";
    process.env.AUTH_SECRET = "테스트-서명키-0123456789abcdef";
  });

  it("맞으면 기기 번호, 고치거나 만료되면 null", async () => {
    const id = newDeviceId();
    const 표 = await createDeviceToken(id);
    expect(await verifyDeviceToken(표)).toBe(id);
    expect(await verifyDeviceToken(표.slice(0, -2) + "xx")).toBeNull();
    expect(
      await verifyDeviceToken(표, Date.now() + 400 * 86_400_000),
    ).toBeNull();
    expect(await verifyDeviceToken(undefined)).toBeNull();
  });

  it("세션 쿠키로 쓸 수 없고, 세션 쿠키도 기기 표가 아니다", async () => {
    const 표 = await createDeviceToken(newDeviceId());
    expect(await verifySessionToken(표)).toBe(false);
    expect(await verifyDeviceToken(await createSessionToken())).toBeNull();
  });

  it("비밀번호를 바꾸면 무효", async () => {
    const 표 = await createDeviceToken(newDeviceId());
    process.env.APP_PASSWORD = "9999";
    expect(await verifyDeviceToken(표)).toBeNull();
  });
});

describe("로그인 경로", () => {
  beforeEach(() => {
    process.env.APP_PASSWORD = "0302";
    process.env.AUTH_SECRET = "테스트-서명키-0123456789abcdef";
    부름.열쇠.length = 0;
    부름.믿음.length = 0;
    부름.알림 = 0;
    부름.넘음 = false;
  });
  const 요청 = (password: string, cookie?: string) =>
    new Request("https://example.com/api/auth/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(cookie ? { cookie } : {}),
      },
      body: JSON.stringify({ password }),
    });

  it("성공하면 로그인 경로에만 실리는 기기 표를 준다", async () => {
    const { POST } = await import("@/app/api/auth/login/route");
    const r = await POST(요청("0302"));
    const 쿠키 = r.headers.get("set-cookie") ?? "";
    expect(쿠키).toContain("sm_device=");
    expect(쿠키).toMatch(/sm_device=[^;]+;[^,]*Path=\/api\/auth\/login/);
    expect(부름.믿음).toEqual([false]);
  });

  it("기기 표가 맞으면 기기 번호로 세고 전체 잠금을 보지 않는다, 틀리면 주소로", async () => {
    const { POST } = await import("@/app/api/auth/login/route");
    const id = newDeviceId();
    await POST(요청("9999", `sm_device=${await createDeviceToken(id)}`));
    await POST(요청("9999", "sm_device=forged.value"));
    expect(부름.열쇠).toEqual([`device:${id}`, "1.2.3.4"]);
    expect(부름.믿음).toEqual([true, false]);
  });

  it("기기 표 요청이 전체 문턱을 넘긴 것을 보면 알린다 (25.1425)", async () => {
    const { POST } = await import("@/app/api/auth/login/route");
    부름.넘음 = true;
    await POST(
      요청("9999", `sm_device=${await createDeviceToken(newDeviceId())}`),
    );
    expect(부름.알림).toBe(1);
  });

  it("같은 이름 쿠키가 여럿이면 맞는 것을 찾는다 (25.1425)", async () => {
    const { POST } = await import("@/app/api/auth/login/route");
    const id = newDeviceId();
    await POST(
      요청(
        "9999",
        `sm_device=forged.value; sm_device=${await createDeviceToken(id)}`,
      ),
    );
    expect(부름.열쇠).toEqual([`device:${id}`]);
  });

  it("로그아웃은 기기 표도 지운다 (25.1425)", async () => {
    const { POST } = await import("@/app/api/auth/logout/route");
    const { NextRequest } = await import("next/server");
    const r = await POST(
      new NextRequest("https://example.com/api/auth/logout", {
        method: "POST",
      }),
    );
    expect(r.headers.get("set-cookie") ?? "").toMatch(/sm_device=;/);
  });

  it("기기 표를 device. 로 다시 감싸도 세션이 아니다 (25.1425)", async () => {
    const 표 = await createDeviceToken(newDeviceId());
    expect(await verifySessionToken(`device.${표}`)).toBe(false);
  });
});
