import { beforeEach, describe, expect, it } from "vitest";
import {
  GLOBAL_MAX_FAILURES,
  GLOBAL_WINDOW_MINUTES,
  PER_IP_MAX_FAILURES,
  PER_IP_WINDOW_MINUTES,
  clientIp,
  failureDelayMs,
  hashIp,
} from "@/lib/loginGuard";

beforeEach(() => {
  process.env.AUTH_SECRET = "테스트-서명키-0123456789abcdef";
});

function requestWith(headers: Record<string, string>): Request {
  return new Request("https://example.com/api/auth/login", {
    method: "POST",
    headers,
  });
}

describe("요청 주소 찾기", () => {
  it("x-forwarded-for 의 맨 앞을 쓴다", () => {
    // 프록시를 여러 번 거치면 쉼표로 이어져 온다. 맨 앞이 원래 요청자다.
    const request = requestWith({ "x-forwarded-for": "1.2.3.4, 10.0.0.1, 10.0.0.2" });
    expect(clientIp(request)).toBe("1.2.3.4");
  });

  it("공백을 떼어낸다", () => {
    expect(clientIp(requestWith({ "x-forwarded-for": "  1.2.3.4  " }))).toBe("1.2.3.4");
  });

  it("x-forwarded-for 가 없으면 x-real-ip 를 본다", () => {
    expect(clientIp(requestWith({ "x-real-ip": "5.6.7.8" }))).toBe("5.6.7.8");
  });

  it("둘 다 없으면 unknown", () => {
    expect(clientIp(requestWith({}))).toBe("unknown");
  });

  it("빈 값이면 unknown", () => {
    expect(clientIp(requestWith({ "x-real-ip": "   " }))).toBe("unknown");
  });
});

describe("주소 해시", () => {
  it("같은 주소는 같은 해시가 된다", async () => {
    expect(await hashIp("1.2.3.4")).toBe(await hashIp("1.2.3.4"));
  });

  it("다른 주소는 다른 해시가 된다", async () => {
    expect(await hashIp("1.2.3.4")).not.toBe(await hashIp("1.2.3.5"));
  });

  it("원본 주소가 해시에 남지 않는다", async () => {
    const hash = await hashIp("192.168.1.100");
    expect(hash).not.toContain("192");
    expect(hash).not.toContain("168");
  });

  it("서명키가 다르면 해시도 다르다", async () => {
    const first = await hashIp("1.2.3.4");
    process.env.AUTH_SECRET = "다른-서명키-fedcba9876543210";
    expect(await hashIp("1.2.3.4")).not.toBe(first);
  });

  it("16진수 32자다", async () => {
    expect(await hashIp("1.2.3.4")).toMatch(/^[0-9a-f]{32}$/);
  });
});

describe("실패 지연", () => {
  it("처음에는 짧게 기다린다", () => {
    // 본인이 한 번 틀린 경우 거의 체감되지 않아야 한다
    expect(failureDelayMs(0)).toBeLessThanOrEqual(500);
  });

  it("실패가 쌓일수록 길어진다", () => {
    const delays = [0, 1, 2, 3, 4].map(failureDelayMs);
    for (let i = 1; i < delays.length; i += 1) {
      expect(delays[i]).toBeGreaterThan(delays[i - 1]);
    }
  });

  it("아무리 쌓여도 상한이 있다", () => {
    // 무한정 늘리면 요청이 타임아웃되어 오히려 곤란하다
    expect(failureDelayMs(999)).toBe(failureDelayMs(4));
    expect(failureDelayMs(999)).toBeLessThanOrEqual(5000);
  });
});

describe("잠금 기준이 짧은 비밀번호를 감당하는가", () => {
  it("네 자리 숫자를 다 훑는 데 걸리는 시간이 충분히 길다", () => {
    // 0302 같은 네 자리 숫자는 경우의 수가 1만 가지다.
    // 한 주소에서 창 하나당 최대 시도 횟수로 나눠 보면 필요한 시간이 나온다.
    const combinations = 10_000;
    const windowsNeeded = combinations / PER_IP_MAX_FAILURES;
    const hoursNeeded = (windowsNeeded * PER_IP_WINDOW_MINUTES) / 60;

    // 한 주소에서는 500시간, 20일이 넘게 걸린다
    expect(hoursNeeded).toBeGreaterThan(24 * 14);
  });

  it("전체 잠금이 주소를 바꿔 가며 들어오는 경우를 막는다", () => {
    const combinations = 10_000;
    const windowsNeeded = combinations / GLOBAL_MAX_FAILURES;
    const hoursNeeded = (windowsNeeded * GLOBAL_WINDOW_MINUTES) / 60;

    // 주소를 아무리 바꿔도 500시간이 걸린다
    expect(hoursNeeded).toBeGreaterThan(24 * 14);
  });

  it("본인이 실수로 잠기더라도 오래 기다리지 않는다", () => {
    expect(PER_IP_WINDOW_MINUTES).toBeLessThanOrEqual(30);
  });

  it("전체 잠금 문턱이 주소별 문턱보다 높다", () => {
    // 본인이 몇 번 틀렸다고 전체가 잠기면 곤란하다
    expect(GLOBAL_MAX_FAILURES).toBeGreaterThan(PER_IP_MAX_FAILURES * 2);
  });
});
