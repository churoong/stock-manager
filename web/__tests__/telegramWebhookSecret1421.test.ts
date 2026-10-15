import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { webhookSecretStrong } from "@/lib/telegramQa";

// 웹훅 경로도 켜는 단추와 같은 비밀값 강도 규칙 (docs/infra.md 25.1421, 보안 감사 #4)
describe("웹훅 비밀값 강도", () => {
  it("16자 이상 영문·숫자·_·- 만", () => {
    expect(webhookSecretStrong("a".repeat(16))).toBe(true);
    expect(webhookSecretStrong("a".repeat(15))).toBe(false);
    expect(webhookSecretStrong("a".repeat(16) + "!")).toBe(false);
    expect(webhookSecretStrong(undefined)).toBe(false);
  });

  describe("경로", () => {
    beforeEach(() => {
      vi.resetModules();
      vi.doMock("@/lib/telegram", () => ({
        sendTelegram: async () => undefined,
      }));
      vi.doMock("@/lib/db", () => ({
        execute: async () => ({ columns: [], rows: [], affectedRows: 0 }),
      }));
      vi.stubEnv("TELEGRAM_CHAT_ID", "42");
    });
    afterEach(() => {
      vi.unstubAllEnvs();
      vi.doUnmock("@/lib/telegram");
      vi.doUnmock("@/lib/db");
    });

    it("짧은 비밀값이면 맞혀도 404 — 경로가 없는 것처럼", async () => {
      vi.stubEnv("TELEGRAM_WEBHOOK_SECRET", "short");
      const { POST } = await import("@/app/api/telegram/webhook/route");
      const r = await POST(
        new Request("http://x/api/telegram/webhook", {
          method: "POST",
          headers: { "x-telegram-bot-api-secret-token": "short" },
          body: JSON.stringify({
            message: { text: "/보유", chat: { id: 42, type: "private" } },
          }),
        }),
      );
      expect(r.status).toBe(404);
    });
  });
});
