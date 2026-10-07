/** 신호 0건이라 돌지 않은 스트레스 실행을 화면이 말한다 (docs/infra.md 25.825) */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { stressNoSignalNote } from "@/lib/backtest";

const 실행 = (as_of: string, note = "그날 신호 없음", status = "success") => ({ status, step_log: JSON.stringify({ stocks: 0, as_of, note }) });

describe("스트레스 — 신호 없는 날", () => {
  it("지난 바스켓이라고 말한다", () => {
    expect(stressNoSignalNote(실행("2026-09-30"), "2026-09-24")).toContain("아래는 2026-09-24 바스켓입니다");
  });
  it("결과가 더 새것이거나 보통 실행이면 조용하다", () => {
    expect(stressNoSignalNote(실행("2026-09-24"), "2026-09-24")).toBeNull();
    expect(stressNoSignalNote({ status: "success", step_log: JSON.stringify({ stocks: 3 }) }, "2026-09-24")).toBeNull();
    expect(stressNoSignalNote(실행("2026-09-30", "그날 신호 없음", "failed"), "2026-09-24")).toBeNull();
    expect(stressNoSignalNote(undefined, "2026-09-24")).toBeNull();
    expect(stressNoSignalNote({ status: "success", step_log: "깨짐" }, "2026-09-24")).toBeNull();
  });
  it("배치가 남기는 글자와 같다", () => {
    expect(readFileSync("../batch/jobs/stress.py", "utf-8")).toContain('"note": "그날 신호 없음"');
  });
});

describe("스트레스 옛 판 안내 (25.827)", () => {
  it("배치 판과 같고, 옛 판이면 말한다", async () => {
    const { STRESS_CALC_VERSION, stressVersionNote } = await import("@/lib/backtest");
    expect(readFileSync("../batch/services/stress.py", "utf-8")).toContain(`CALC_VERSION = ${STRESS_CALC_VERSION}`);
    expect(stressVersionNote(1)).toContain("옛 계산 방식");
    expect(stressVersionNote(STRESS_CALC_VERSION)).toBeNull();
    expect(stressVersionNote(null)).toBeNull();
  });
  it("라우트와 화면이 안내를 잇는다", () => {
    expect(readFileSync("app/api/backtest/route.ts", "utf-8")).toContain("stress_note: stressNoSignalNote(");
    expect(readFileSync("components/BacktestView.tsx", "utf-8")).toContain("{data.stress_note}");
  });
});
