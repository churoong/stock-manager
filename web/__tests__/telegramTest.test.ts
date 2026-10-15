import { afterEach, describe, expect, it, vi } from "vitest";

describe("텔레그램 테스트 발송 (docs/infra.md 25.584, 감사)", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("대화방 번호가 비면 보내지 않고 후보만 알린다 — 장중 발송과 같은 조건에서 실패한다", async () => {
    vi.stubEnv("TELEGRAM_BOT_TOKEN", "t");
    vi.stubEnv("TELEGRAM_CHAT_ID", "");
    const 부른것: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      부른것.push(url);
      return new Response(JSON.stringify({ ok: true, result: [{ message: { chat: { id: 42 } } }] }));
    }));
    const { POST } = await import("@/app/api/telegram/test/route");
    const res = await POST();
    const body = (await res.json()) as { error?: string };
    expect(res.status).toBe(500);
    expect(body.error).toContain("TELEGRAM_CHAT_ID 가 비어 있어 보내지 않았습니다");
    expect(body.error).toContain("42");
    expect(부른것.some((u) => u.includes("sendMessage")), "모르는 대화방에 보내면 안 된다").toBe(false);
  });

  it("토큰 앞뒤 공백을 빼고, 제한 시간을 둔다 (25.1387)", async () => {
    vi.stubEnv("TELEGRAM_BOT_TOKEN", "123:abc ");
    vi.stubEnv("TELEGRAM_CHAT_ID", "42");
    const 부른것: Array<{ url: string; signal: unknown }> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      부른것.push({ url, signal: init?.signal });
      return new Response(JSON.stringify({ ok: true, result: {} }));
    }));
    const { POST } = await import("@/app/api/telegram/test/route");
    await POST();
    expect(부른것.length).toBeGreaterThan(0);
    for (const 부름 of 부른것) {
      expect(부름.url.startsWith("https://api.telegram.org/bot123:abc/")).toBe(true);
      expect(부름.signal, "매달리면 단추가 함수 상한까지 돈다").toBeInstanceOf(AbortSignal);
    }
  });
});
