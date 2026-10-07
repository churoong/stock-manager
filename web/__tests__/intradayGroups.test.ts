/** 장중 알림 발송: 한 통 묶음, 먼저 잡기, 다른 날 알림 날짜 (docs/infra.md 25.538, 감사 재현). */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { alertGroups, bundleMessage } from "@/lib/intraday";
import { SAFE_LEN } from "@/lib/telegram";

const 지금 = new Date("2026-10-20T00:10:00Z"); // 09:10 KST

describe("장중 알림 발송 (25.538)", () => {
  it("묶음마다 한 통에 들어간다 — 둘째 통이 실패해도 첫 통을 다시 보내지 않게", () => {
    const 알림 = Array.from({ length: 50 }, (_, i) => ({
      market: "US", created_at: "2026-10-19T14:00:00Z",
      message: `ALERT#${String(i).padStart(2, "0")} Very Long Company Name Holdings International Incorporated: 손절선 $150.00 터치 (저가 $149.00, 현재 $149.50)`,
    }));
    const 묶음 = alertGroups(알림, 지금, "머리말", SAFE_LEN);
    expect(묶음.length).toBeGreaterThan(1);
    expect(묶음.flat()).toEqual(알림);
    묶음.forEach((g, i) => expect(bundleMessage(g, 지금, i === 0 ? "머리말" : null).length + 300).toBeLessThanOrEqual(SAFE_LEN));
  });

  it("다른 날 만든 알림은 날짜를 붙인다", () => {
    const 글 = bundleMessage([
      { market: "US", created_at: "2026-10-17T13:00:00Z", message: "Apple: 손절선 터치" },
      { market: "KR", created_at: "2026-10-20T00:05:00Z", message: "가: 급등" },
    ], 지금);
    expect(글).toContain("[미국 10-17 22:00] Apple");
    expect(글).toContain("[국내 09:05] 가");
  });

  it("경로는 잡은 알림만 보내고 묶음마다 표시한다", () => {
    const 경로 = readFileSync("app/api/cron/intraday/route.ts", "utf8");
    expect(경로).toContain("AND COALESCE(sent_at, '') = ?");
    expect(경로).toContain("alertGroups(pending, now, 머리, SAFE_LEN)");
    // 보낸 뒤 발송 표시는 몇 번 더 시도한다 — 한 번 실패로 10분 뒤 같은 알림이 다시 나갔다 (25.551)
    expect(경로).toMatch(/if \(시도 >= MARK_SENT_TRIES\) throw e;/);
    expect(경로).toContain("setTimeout(r, 시도 * MARK_SENT_BACKOFF_MS)"); // 연달아 부르지 않는다 (25.555)
  });
});

describe("알림 센터 발송 칸 (25.547)", () => {
  it("잡힌 알림은 Invalid Date 가 아니라 보내는 중", async () => {
    const { sentLabel } = await import("@/lib/alerts");
    const kst = (iso: string) => new Date(iso).toISOString();
    expect(sentLabel("claim:2026-10-20T00:10:00.000Z", kst)).toContain("보내는 중");
    expect(sentLabel(null, kst)).toBe("아직 안 보냄");
    expect(sentLabel("2026-10-20T00:10:00.000Z", kst)).toBe("발송 2026-10-20T00:10:00.000Z");
  });
});
