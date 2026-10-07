import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { FRESHNESS, WATCHES } from "@/lib/health";

// docs/infra.md 25.917 (감사: 알림 화면·상태 화면)
describe("알림 화면의 장중 감시 판정은 자료를 읽은 시각으로 (25.917)", () => {
  it("응답에 만든 시각이 있고 화면이 그것으로 판정한다", () => {
    expect(readFileSync("app/api/alerts/route.ts", "utf-8")).toContain("server_time: new Date().toISOString()");
    const 글 = readFileSync("components/AlertCenter.tsx", "utf-8");
    expect(글).toContain("const now = readAt ?? new Date();");
  });
});

describe("국내 무응답 알림이 같은 날 예비 실행을 말한다 (25.917)", () => {
  it("09:35 재시도 안내가 붙는다", () => {
    const kr = WATCHES.find((w) => w.job === "daily_kr");
    expect(kr?.retryNote).toContain("09:35");
    expect(kr?.dueOffsetMinutes).toBe(20); // 마감은 그대로 — 그 시각에 리포트가 없는 것은 사실이다
    expect(readFileSync("lib/health.ts", "utf-8")).toContain("watch.retryNote ? `\\n${watch.retryNote}` : \"\"");
  });
});

describe("점수 신선도는 거래일마다 (25.917)", () => {
  it("25.889 뒤로 일일 배치가 매일 낸다 — 주간 잣대면 14일까지 초록", () => {
    for (const k of ["scores_kr", "scores_us"]) expect(FRESHNESS.find((r) => r.key === k)?.every).toBe("session");
    // 신호는 0건인 날이 정상이라 그대로
    expect(FRESHNESS.find((r) => r.key === "signals_kr")?.every).toBe("week");
  });
});
