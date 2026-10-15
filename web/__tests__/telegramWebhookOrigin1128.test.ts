import { describe, expect, it } from "vitest";
import { ANSWER_TIMEOUT_MS, webhookOrigin } from "@/lib/telegramQa";

/** 웹훅을 운영 대표 주소에 건다 — 배포 고유 주소에서 눌러도 (docs/infra.md 25.1128) */
describe("웹훅 출처", () => {
  it("운영 대표 주소가 있으면 그것", () => {
    expect(webhookOrigin("https://app-abc123-team.vercel.app/api/telegram/webhook-setup", "app.example.com")).toBe("https://app.example.com");
    expect(webhookOrigin("https://x.vercel.app/a", "https://app.example.com/")).toBe("https://app.example.com");
  });
  it("없으면 요청 출처", () => {
    expect(webhookOrigin("https://app-abc123-team.vercel.app/api/x", undefined)).toBe("https://app-abc123-team.vercel.app");
    expect(webhookOrigin("https://a.example/api/x", "  ")).toBe("https://a.example");
  });
});

/** 웹훅 함수 상한이 답 만들기 + 발송 제한보다 길다 (docs/infra.md 25.1387) */
describe("웹훅 실행 상한", () => {
  it("답 상한 8초 + 발송 10초 + 여유보다 길게 선언한다", async () => {
    const { maxDuration } = await import("@/app/api/telegram/webhook/route");
    expect(maxDuration).toBeGreaterThanOrEqual(ANSWER_TIMEOUT_MS / 1000 + 10 + 5);
  });
});
