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
});
