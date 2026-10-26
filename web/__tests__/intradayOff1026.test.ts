/** 장중 감시 끔 (docs/infra.md 25.1026) — 사용자 "장중 감시는 일단 빼자. 디비 소모가 너무 많아" */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const execute = vi.fn();
vi.mock("@/lib/db", async (orig) => ({ ...(await orig<typeof import("@/lib/db")>()), execute, batch: vi.fn() }));

describe("장중 감시 꺼짐", () => {
  beforeEach(() => {
    execute.mockReset();
    process.env.CRON_SECRET = "s".repeat(40);
  });
  afterEach(() => {
    vi.useRealTimers(); // 단언이 실패해도 가짜 시계가 다음 시험으로 새지 않게
  });

  it("운영 기본값은 꺼짐이다", async () => {
    const { INTRADAY_ENABLED } = await import("@/lib/intradaySwitch");
    expect(INTRADAY_ENABLED).toBe(false);
  });

  it("경로는 토큰만 보고 DB 를 전혀 건드리지 않는다", async () => {
    // 장중 손절·익절 감시 깨우기(25.1807)만은 스위치와 따로 국내 평일 KST 09:20~16:10 에 한 행짜리 읽기를 한다 — 그 밖 시각으로
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-08T12:00:00Z"));
    const { GET } = await import("@/app/api/cron/intraday/route");
    const res = await GET(new Request("http://x/api/cron/intraday?market=KR", { headers: { "x-cron-secret": "s".repeat(40) } }));
    expect(res.status).toBe(200);
    expect((await res.json()).skipped).toContain("장중 감시 꺼짐");
    expect(execute).not.toHaveBeenCalled();
    // 토큰이 틀리면 여전히 막는다
    const bad = await GET(new Request("http://x/api/cron/intraday?market=KR", { headers: { "x-cron-secret": "x" } }));
    expect(bad.status).toBe(401);
  });

  it("화면은 빨간 멈춤이 아니라 꺼짐으로 말한다", async () => {
    const { cronVerdict, intradayVerdict } = await import("@/lib/health");
    const now = new Date("2026-10-08T03:00:00Z");
    expect(intradayVerdict("KR", undefined, true, now)).toMatchObject({ tone: "mute", text: "꺼짐" });
    expect(cronVerdict({ job: "intraday", market: "KR", called_at: "2026-10-07T03:00:00Z", outcome: "never" }, now).tone).toBe("mute");
  });
});
