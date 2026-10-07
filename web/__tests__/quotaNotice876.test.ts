/** 한도 날 크론 경로가 멈췄다는 것을 UTC 하루 한 번만 알린다 (docs/infra.md 25.876) */
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

describe("DB 한도 알림", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.TELEGRAM_BOT_TOKEN;
    delete process.env.TELEGRAM_CHAT_ID;
  });

  it("같은 UTC 날에는 한 번, 다음 날 다시", async () => {
    process.env.TELEGRAM_BOT_TOKEN = "t";
    process.env.TELEGRAM_CHAT_ID = "1";
    const 본문: string[] = [];
    vi.stubGlobal("fetch", async (_u: string, init: RequestInit) => {
      본문.push(JSON.parse(String(init.body)).text);
      return new Response('{"ok":true,"result":{"message_id":1}}');
    });
    const { notifyQuotaOnce, resetQuotaNotice } = await import("@/lib/quotaNotice");
    resetQuotaNotice();
    const 사유 = "Cloudflare D1 하루 읽기 한도(500만 행)에 걸렸습니다";
    for (let i = 0; i < 30; i += 1) await notifyQuotaOnce("장중 감시", 사유, new Date(Date.UTC(2026, 9, 1, 5, i)));
    expect(본문.length).toBe(1);
    expect(본문[0]).toContain("장중 감시");
    await notifyQuotaOnce("장중 감시", 사유, new Date(Date.UTC(2026, 9, 2, 0, 1)));
    expect(본문.length).toBe(2);
    resetQuotaNotice();
  });

  it("크론 네 경로가 한도 갈래에서 부른다", () => {
    for (const p of ["intraday", "news", "news-kr", "health"]) {
      const 글 = readFileSync(`app/api/cron/${p}/route.ts`, "utf-8");
      expect(글, p).toMatch(/if \(quota\) \{[\s\S]{0,400}?notifyQuotaOnce\(/);
    }
  });
});
