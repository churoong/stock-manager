import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { monthWindow, pickUsageRows } from "@/lib/health";

describe("상태 화면 감사 (docs/infra.md 25.593)", () => {
  it("지난달은 월말에도 빠뜨리지 않는다", () => {
    expect(monthWindow(new Date("2026-10-31T12:00:00Z"))).toBe("2026-09");
    expect(monthWindow(new Date("2026-03-30T00:00:00Z"))).toBe("2026-02");
    expect(monthWindow(new Date("2026-01-05T00:00:00Z"))).toBe("2025-12");
  });

  it("API·창마다 가장 새 창 하나만, 지금 창인지 표시한다 — 지난 날 100% 를 오늘로 보이지 않는다", () => {
    const rows = [
      { api_name: "d1_writes", window_type: "day", window_start: "2026-09-26" },
      { api_name: "d1_writes", window_type: "day", window_start: "2026-09-27" },
      { api_name: "turso_reads", window_type: "month", window_start: "2026-08" },
    ];
    const 고른 = pickUsageRows(rows, new Date("2026-09-28T01:00:00Z"));
    expect(고른.map((r) => [r.api_name, r.window_start, r.current])).toEqual([
      ["d1_writes", "2026-09-27", false],
      ["turso_reads", "2026-08", false],
    ]);
    expect(pickUsageRows([{ api_name: "x", window_type: "day", window_start: "2026-09-28" }], new Date("2026-09-28T01:00:00Z"))[0].current).toBe(true);
  });

  it("화면: 막힘 상태를 먼저 보고, 요청한 작업은 잠그고, 못 읽은 알림·대기열을 말한다", () => {
    const 화면 = readFileSync("components/StatusView.tsx", "utf8");
    expect(화면).toContain('u.state === "blocked" ? "blocked"');
    expect(화면).toContain('못읽음("health_alerts")');
    expect(화면).toContain('못읽음("adjust_queue")');
  });

  it("무응답 알림은 먼저 잡고 보낸다", () => {
    expect(readFileSync("app/api/cron/health/route.ts", "utf8")).toContain("WHERE id = ? AND sent_at IS NULL");
  });
});

it("용량 같은 수위 행은 창 날짜가 지나도 지금 값으로 친다 (25.594)", () => {
  const [행] = pickUsageRows([{ api_name: "d1_db_size", window_type: "day", window_start: "2026-09-28" }], new Date("2026-09-29T03:00:00Z"));
  expect(행.current).toBe(true);
});

it("수위 행도 몇 주 묵으면 지난 창이다 (25.596)", () => {
  const [행] = pickUsageRows([{ api_name: "d1_db_size", window_type: "day", window_start: "2026-09-01" }], new Date("2026-09-29T03:00:00Z"));
  expect(행.current).toBe(false);
});

it("지금 쓰는 DB 를 적는다 (25.599)", () => {
  expect(readFileSync("components/StatusView.tsx", "utf8")).toContain("지금 쓰는 DB: {data.db_backend === \"d1\"");
});

