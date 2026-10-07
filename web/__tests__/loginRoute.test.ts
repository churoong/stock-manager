import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 로그인 경로 테스트.
 *
 * DB 를 타지 않도록 loginGuard 를 가짜로 바꾼다.
 * 확인하려는 것은 설정이 빠졌을 때 무엇이 없는지 알려주는가다.
 * 실제로 AUTH_SECRET 이 빠져 빈 500 만 돌아오는 바람에 원인을 찾느라 헤맸다.
 */

vi.mock("@/lib/loginGuard", () => ({
  clientIp: () => "1.2.3.4",
  hashIp: async () => "해시",
  checkGuard: async () => ({ allowed: true, recentFailures: 0 }),
  reserveAttempt: async () => ({ allowed: true, recentFailures: 0 }),
  cleanupAttempts: async () => {},
  recordAttempt: async () => {},
  failureDelayMs: () => 0,
  alertGlobalLock: async () => {},
}));

const { POST } = await import("@/app/api/auth/login/route");

function loginRequest(password: string): Request {
  return new Request("https://example.com/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
}

beforeEach(() => {
  process.env.APP_PASSWORD = "0302";
  process.env.AUTH_SECRET = "테스트-서명키-0123456789abcdef";
});

describe("로그인", () => {
  it("맞는 비밀번호면 쿠키를 준다", async () => {
    const response = await POST(loginRequest("0302"));

    expect(response.status).toBe(200);
    expect(response.headers.get("set-cookie")).toContain("sm_session=");
  });

  it("틀리면 401 이고 쿠키를 주지 않는다", async () => {
    const response = await POST(loginRequest("9999"));

    expect(response.status).toBe(401);
    expect(response.headers.get("set-cookie") ?? "").not.toContain("sm_session=");
  });

  it("실패 이유를 나누어 알려주지 않는다", async () => {
    const response = await POST(loginRequest("9999"));
    const body = await response.json();

    // 비밀번호가 하나뿐이라 알려줄 것도 없고, 알려주면 공격자에게만 도움이 된다
    expect(body.error).toBe("비밀번호가 맞지 않습니다");
  });

  it("서명키가 없으면 무엇이 없는지 알려준다", async () => {
    delete process.env.AUTH_SECRET;

    const response = await POST(loginRequest("0302"));
    const body = await response.json();

    expect(response.status).toBe(500);
    expect(body.error).toContain("AUTH_SECRET");
    expect(body.hint).toContain("재배포");
  });

  it("서명키가 없어도 틀린 비밀번호는 여전히 401 이다", async () => {
    delete process.env.AUTH_SECRET;

    const response = await POST(loginRequest("9999"));

    // 설정이 빠졌다고 아무나 들여보내면 안 된다
    expect(response.status).toBe(401);
  });

  it("비밀번호 설정이 비어 있으면 아무도 못 들어온다", async () => {
    process.env.APP_PASSWORD = "";

    expect((await POST(loginRequest(""))).status).toBe(401);
    expect((await POST(loginRequest("0302"))).status).toBe(401);
  });

  it("본문이 깨져도 500 이 아니라 401 이다", async () => {
    const broken = new Request("https://example.com/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "이건 JSON 이 아니다",
    });

    expect((await POST(broken)).status).toBe(401);
  });
});
