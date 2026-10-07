import { beforeEach, describe, expect, it } from "vitest";
import {
  checkPassword,
  createSessionToken,
  timingSafeEqual,
  verifySessionToken,
} from "@/lib/auth";

const SECRET = "테스트용-서명키-0123456789abcdef";

beforeEach(() => {
  process.env.AUTH_SECRET = SECRET;
  process.env.APP_PASSWORD = "충분히-긴-비밀번호-0123456789";
});

describe("세션 토큰", () => {
  it("발급한 토큰은 검증을 통과한다", async () => {
    const token = await createSessionToken();
    expect(await verifySessionToken(token)).toBe(true);
  });

  it("없는 토큰은 거부한다", async () => {
    expect(await verifySessionToken(undefined)).toBe(false);
    expect(await verifySessionToken("")).toBe(false);
  });

  it("형식이 깨진 토큰은 거부한다", async () => {
    expect(await verifySessionToken("서명없음")).toBe(false);
    expect(await verifySessionToken(".서명만")).toBe(false);
  });

  it("본문을 고치면 거부한다", async () => {
    const token = await createSessionToken();
    const [, signature] = token.split(".");
    const forged = `${btoa(JSON.stringify({ exp: Date.now() + 999999 }))}.${signature}`;

    expect(await verifySessionToken(forged)).toBe(false);
  });

  it("서명을 고치면 거부한다", async () => {
    const token = await createSessionToken();
    const [payload] = token.split(".");

    expect(await verifySessionToken(`${payload}.가짜서명`)).toBe(false);
  });

  it("다른 서명키로 만든 토큰은 거부한다", async () => {
    const token = await createSessionToken();

    process.env.AUTH_SECRET = "다른-서명키-fedcba9876543210";
    expect(await verifySessionToken(token)).toBe(false);
  });

  it("만료된 토큰은 거부한다", async () => {
    const past = Date.now() - 365 * 24 * 60 * 60 * 1000;
    const token = await createSessionToken(past);

    expect(await verifySessionToken(token)).toBe(false);
  });

  it("만료 직전에는 통과한다", async () => {
    const now = Date.now();
    const token = await createSessionToken(now);

    // 30일 유효. 29일 뒤에는 아직 살아 있어야 한다
    const almost = now + 29 * 24 * 60 * 60 * 1000;
    expect(await verifySessionToken(token, almost)).toBe(true);
  });

  it("30일이 지나면 거부한다", async () => {
    const now = Date.now();
    const token = await createSessionToken(now);

    const later = now + 31 * 24 * 60 * 60 * 1000;
    expect(await verifySessionToken(token, later)).toBe(false);
  });
});

describe("비밀번호", () => {
  it("맞으면 통과한다", () => {
    expect(checkPassword("충분히-긴-비밀번호-0123456789")).toBe(true);
  });

  it("틀리면 거부한다", () => {
    expect(checkPassword("틀린비밀번호")).toBe(false);
  });

  it("빈 값은 거부한다", () => {
    expect(checkPassword("")).toBe(false);
  });

  it("설정이 비어 있으면 아무도 못 들어온다", () => {
    process.env.APP_PASSWORD = "";
    expect(checkPassword("")).toBe(false);
    expect(checkPassword("아무거나")).toBe(false);
  });

  it("네 자리 비밀번호를 받는다", () => {
    // 짧은 비밀번호를 허용하는 대신 lib/loginGuard.ts 가 시도 횟수를 막는다
    process.env.APP_PASSWORD = "0302";
    expect(checkPassword("0302")).toBe(true);
    expect(checkPassword("0303")).toBe(false);
  });

  it("최소 길이 미만은 거부한다", () => {
    process.env.APP_PASSWORD = "12";
    expect(checkPassword("12")).toBe(false);
  });

  it("앞부분만 같아도 거부한다", () => {
    expect(checkPassword("충분히-긴-비밀번호-012345678")).toBe(false);
  });
});

describe("시간 일정 비교", () => {
  it("같으면 참", () => {
    expect(timingSafeEqual("abc", "abc")).toBe(true);
  });

  it("다르면 거짓", () => {
    expect(timingSafeEqual("abc", "abd")).toBe(false);
  });

  it("길이가 달라도 예외를 내지 않는다", () => {
    expect(timingSafeEqual("abc", "abcdef")).toBe(false);
    expect(timingSafeEqual("", "abc")).toBe(false);
  });

  it("한글도 바르게 비교한다", () => {
    expect(timingSafeEqual("비밀번호", "비밀번호")).toBe(true);
    expect(timingSafeEqual("비밀번호", "비밀번효")).toBe(false);
  });
});

describe("비밀번호를 바꾸면 그 전 세션이 끊긴다 (docs/infra.md 25.395)", () => {
  it("옛 비밀번호로 받은 토큰은 새 비밀번호에서 거부한다", async () => {
    const token = await createSessionToken();
    expect(await verifySessionToken(token)).toBe(true);

    process.env.APP_PASSWORD = "새로-바꾼-비밀번호-9876543210";
    expect(await verifySessionToken(token)).toBe(false);
  });

  it("비밀번호 원문은 토큰에 들어가지 않는다", async () => {
    const token = await createSessionToken();
    const [payload] = token.split(".");
    const decoded = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    expect(decoded).not.toContain("비밀번호");
    expect(token).not.toContain(process.env.APP_PASSWORD as string);
  });
});
