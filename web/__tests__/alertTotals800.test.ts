/** 알림 목록 상한 밖을 말하고, 미국 알림에 장 날짜를 붙인다 (docs/infra.md 25.800, 알림 감사 #2·#4) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { ALERT_TOTALS, alertsTabLabel, hiddenUnreadNote, sessionLabel } from "@/lib/alerts";

const 행 = (is_read: number) => ({ is_read });

describe("알림 수", () => {
  it("목록이 잘렸으면 전체 수와 전체 안 읽음을 적는다", () => {
    const 보임 = [...Array(190).fill(행(1)), ...Array(10).fill(행(0))];
    expect(alertsTabLabel(보임, { total: 350, unread: 30 })).toBe("알림 최근 200 / 전체 350 (안 읽음 30)");
    expect(hiddenUnreadNote(보임, { total: 350, unread: 30 })).toContain("안 읽은 알림 20건이 더 있습니다");
  });
  it("잘리지 않았으면 예전 모양, 전체 수를 모르면 목록 안에서 센다", () => {
    expect(alertsTabLabel([행(0), 행(1)], { total: 2, unread: 1 })).toBe("알림 2 (안 읽음 1)");
    expect(alertsTabLabel([행(0), 행(1)], null)).toBe("알림 2 (안 읽음 1)");
    expect(hiddenUnreadNote([행(0)], { total: 1, unread: 1 })).toBeNull();
  });
  it("전체 질의", () => {
    const d = new DatabaseSync(":memory:");
    for (const f of readdirSync(join(process.cwd(), "..", "migrations")).filter((x) => x.endsWith(".sql")).sort()) {
      d.exec(readFileSync(join(process.cwd(), "..", "migrations", f), "utf-8"));
    }
    expect(d.prepare(ALERT_TOTALS).get()).toEqual({ total: 0, unread: 0 });
  });
});

describe("미국 알림의 장 날짜", () => {
  it("미국만, 그 장의 날짜", () => {
    expect(sessionLabel("US", "2026-09-30")).toBe(" · 9/30장");
    expect(sessionLabel("KR", "2026-09-30")).toBe("");
  });
  it("경로와 화면이 쓴다", () => {
    expect(readFileSync("app/api/alerts/route.ts", "utf-8")).toContain("execute(ALERT_TOTALS).catch(() => null)");
    const 화면 = readFileSync("components/AlertCenter.tsx", "utf-8");
    expect(화면).toContain("alertsTabLabel(alerts, totals)");
    expect(화면).toContain("sessionLabel(a.market, a.trade_date)");
    // 읽음 처리할 때 전체 안 읽음 수도 줄인다 (25.805, 교차검증)
    expect(화면).toContain("setTotals((t) => (t ? { ...t, unread: Math.max(0, t.unread - 새로읽음) } : t));");
  });
});
