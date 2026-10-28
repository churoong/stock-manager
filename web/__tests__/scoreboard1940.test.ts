/**
 * 판정판 표시 (docs/funnel.md 2.2, 기록 25.1940) — 배치가 `step_log.scoreboard` 에 실은 줄을 읽어 꼴만 바꾼다.
 * 질의는 실제 마이그레이션을 적용한 SQLite 에 돌린다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeAll, describe, expect, it } from "vitest";
import {
  LATEST_SCOREBOARD, SCOREBOARD_OF_GROUP, parseScoreboard, pickScoreboard, scoreLine, scoreboardHead, type ScoreboardRun,
} from "@/lib/scoreboard";

const 줄 = {
  strategy: "core_h", market: "KR", cohort: "1b", trials_cumulative: 17, cagr: 0.1234, excess_cagr: -0.0101, mdd: -0.2567,
  dsr: 0.9312, dsr_n: 17, control_lower: 51.04, gate_lower: null, half_first_cagr: 0.05, half_second_cagr: null,
  months: 48, rebalances: 1000, buys: 120, control_blocks: 9, gate_blocks: 4, sells_scored: 80,
  missing: { gate_lower: "하한 없음 — 묶음 4개(block_bound 최소 미달)", half_second_cagr: "second 반 CAGR 표본 미달" },
};

describe("판정판 글 (25.1940)", () => {
  it("값은 꼴만 바꾼다 — 비율은 %, 분위 하한 소수 한 자리, DSR 두 자리와 N", () => {
    const l = scoreLine(parseScoreboard([줄]).rows[0]);
    expect(l.label).toBe("핵심 H (합의)");
    expect(l.cohort).toBe("코호트 1b");
    expect(Object.fromEntries(l.cells)).toEqual({
      CAGR: "+12.3%", "초과 CAGR": "-1.0%", MDD: "-25.7%", DSR: "0.93 (N 17)", "대조 하한": "51.0", "관문 하한": "없음",
      "앞 반": "+5.0%", "뒤 반": "없음",
    });
    expect(l.sample).toBe("48개월 · 리밸런스 1000 · 매수 120 · 묶음 대조 9/관문 4 · 판 뒤 매도 80 · 누적 시도 17");
  });

  it("빈 칸은 '없음: 칸 — 까닭' 으로, 같은 까닭은 한 줄로 묶는다", () => {
    expect(scoreLine(parseScoreboard([줄]).rows[0]).missing).toEqual([
      "없음: 관문 하한 — 하한 없음 — 묶음 4개(block_bound 최소 미달)",
      "없음: 뒤 반 — second 반 CAGR 표본 미달",
    ]);
    const 월 = {
      ...줄, strategy: "composite", cohort: null, control_lower: null, gate_lower: null, buys: null,
      missing: {
        cohort: "기본 파라미터 실행 아님 — 판정 코호트 밖(docs/factors.md 12장 머리)",
        control_lower: "월 백테스트는 결정 원장이 없어 결정 단위 판정을 내지 않는다",
        gate_lower: "월 백테스트는 결정 원장이 없어 결정 단위 판정을 내지 않는다",
        buys: "결정 단위 증거 없음(월 백테스트 또는 판정 실패)",
      },
    };
    const l = scoreLine(parseScoreboard(JSON.stringify([월])).rows[0]);
    expect(l.label).toBe("종합 점수");
    expect(l.cohort).toBe("코호트 밖");
    expect(l.missing).toEqual([
      "없음: 코호트 — 기본 파라미터 실행 아님 — 판정 코호트 밖(docs/factors.md 12장 머리)",
      "없음: 대조 하한·관문 하한 — 월 백테스트는 결정 원장이 없어 결정 단위 판정을 내지 않는다",
      "없음: 매수 — 결정 단위 증거 없음(월 백테스트 또는 판정 실패)",
    ]);
    expect(l.sample).toContain("매수 없음");
    // 까닭 없이 빈 칸(옛 기록·모양 틀림)도 "없음" 이고 빠진 까닭이라고 적는다
    const 빈 = scoreLine(parseScoreboard([{ strategy: "x", missing: { cagr: "" } }]).rows[0]);
    expect(Object.fromEntries(빈.cells).CAGR).toBe("없음");
    expect(빈.missing).toEqual(["없음: CAGR — 까닭 기록 없음"]);
  });

  it("모양이 틀린 줄은 버리고 센다 — 화면이 죽지 않는다", () => {
    expect(parseScoreboard(JSON.stringify([줄, { cagr: 1 }, "x"]))).toMatchObject({ unreadable: 2 });
    expect(parseScoreboard(JSON.stringify([줄, { cagr: 1 }])).rows).toHaveLength(1);
    expect(parseScoreboard("{")).toEqual({ rows: [], unreadable: 1 });
    expect(parseScoreboard(null)).toEqual({ rows: [], unreadable: 0 });
    expect(parseScoreboard('{"a": 1}')).toEqual({ rows: [], unreadable: 1 });
    // 숫자 칸에 글이 와도 그 칸만 비운다
    expect(parseScoreboard([{ ...줄, cagr: "12%" }]).rows[0].cagr).toBeNull();
  });

  it("머리 줄 — 고른 묶음 것이 아니면 그렇다고 적는다", () => {
    const run = { job_name: "backtest_explore", started_at: "2026-11-03T08:00:00+00:00" };
    expect(scoreboardHead(run, false)).toBe("탐색 실행 (2026-11-03 17:00 KST)");
    // 물러난 까닭을 단정하지 않는다 — 25.1940 전 실행만이 아니라 도는 중·실패·기록 깨짐도 같은 길이다(25.1941)
    const 머리 = scoreboardHead(run, true);
    expect(머리).toContain("판정판을 찾지 못해 가장 최근 판정판(탐색, 2026-11-03 17:00 KST)");
    expect(머리).toContain("25.1940 전 실행·도는 중·실패·기록 깨짐 가운데 하나");
    expect(머리).not.toContain("(25.1940 전 실행)");
  });

  it("어느 판정판을 보이나 — 고른 묶음 것, 실패 까닭만 있어도 그것, 없으면 최근으로 물러나고, 둘 다 없으면 없음 (25.1941)", () => {
    const 칸 = (o: Partial<ScoreboardRun>): ScoreboardRun =>
      ({ job_name: "backtest", started_at: "2026-11-03T08:00:00Z", scoreboard: null, scoreboard_error: null, ...o });
    const 묶음 = 칸({ job_name: "backtest_core", scoreboard: "[]" });
    const 실패 = 칸({ job_name: "backtest_explore", scoreboard_error: "RuntimeError: 망" });
    const 옛 = 칸({});
    const 최근 = 칸({ started_at: "2026-11-04T08:00:00Z", scoreboard: JSON.stringify([줄]) });
    // 빈 배열도 "실린" 판정판이다 — 최근으로 물러나지 않는다
    expect(pickScoreboard(묶음, 최근)).toEqual({ run: 묶음, fallback: false });
    expect(pickScoreboard(실패, 최근)).toEqual({ run: 실패, fallback: false });
    expect(pickScoreboard(옛, 최근)).toEqual({ run: 최근, fallback: true });
    expect(pickScoreboard(undefined, 최근)).toEqual({ run: 최근, fallback: true });
    expect(pickScoreboard(undefined, undefined)).toBeNull();
    expect(pickScoreboard(옛, undefined)).toBeNull();
  });
});

describe("판정판 질의 (25.1940)", () => {
  let db: DatabaseSync;
  beforeAll(() => {
    db = new DatabaseSync(":memory:");
    const dir = join(process.cwd(), "..", "migrations");
    for (const f of readdirSync(dir).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(dir, f), "utf-8"));
    const ins = db.prepare(
      `INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status, step_log)
       VALUES (?, ?, '2026-11-03', 'manual', ?, 'success', ?)`,
    );
    ins.run("backtest_core", "KR", "2026-11-01T00:00:00Z", JSON.stringify({ group_id: "g_core", scoreboard: [줄] }));
    ins.run("backtest", "KR", "2026-11-03T00:00:00Z", JSON.stringify({ group_id: "g_old" }));  // 25.1940 전 실행
    ins.run("backtest_explore", "KR", "2026-11-02T00:00:00Z", JSON.stringify({ group_id: "g_bad", scoreboard_error: "RuntimeError: 망" }));
    ins.run("backtest", "KR", "2026-11-04T00:00:00Z", "{깨진");  // JSON 아닌 옛 기록 — 질의를 죽이면 안 된다
    ins.run("backtest", "US", "2026-11-05T00:00:00Z", JSON.stringify({ group_id: "g_us", scoreboard: [] }));
    ins.run("stress", "KR", "2026-11-06T00:00:00Z", JSON.stringify({ group_id: "g_core", scoreboard: [] }));
  });

  it("고른 묶음의 판정판 — 두 칸만, 그 시장·백테스트 작업만", () => {
    const r = db.prepare(SCOREBOARD_OF_GROUP).get("KR", "g_core") as unknown as ScoreboardRun;
    expect(r.job_name).toBe("backtest_core");
    expect(parseScoreboard(r.scoreboard).rows.map((x) => x.strategy)).toEqual(["core_h"]);
    expect(r.scoreboard_error).toBeNull();
    const 실패 = db.prepare(SCOREBOARD_OF_GROUP).get("KR", "g_bad") as unknown as ScoreboardRun;
    expect(실패.scoreboard).toBeNull();
    expect(실패.scoreboard_error).toBe("RuntimeError: 망");
    const 옛 = db.prepare(SCOREBOARD_OF_GROUP).get("KR", "g_old") as unknown as ScoreboardRun;
    expect(옛.scoreboard).toBeNull();
    expect(옛.scoreboard_error).toBeNull();
    expect(db.prepare(SCOREBOARD_OF_GROUP).get("KR", "g_us")).toBeUndefined();
  });

  it("물러날 때는 판정판이 실린 가장 최근 실행 — 다른 시장·다른 작업·깨진 기록은 건너뛴다", () => {
    const r = db.prepare(LATEST_SCOREBOARD).get("KR") as unknown as ScoreboardRun;
    expect(r.job_name).toBe("backtest_core");
    expect((db.prepare(LATEST_SCOREBOARD).get("US") as unknown as ScoreboardRun).scoreboard).toBe("[]");
  });
});


describe("판정판 안내 줄은 가리킬 곳이 있을 때만 (25.1942)", () => {
  it("월 묶음·경고 있음이면 둘 다, 핵심 묶음·경고 없음이면 없음, 물러남이면 다른 실행이라고", async () => {
    const { elsewhereNote } = await import("@/lib/scoreboard");
    const 둘다 = elsewhereNote(["composite", "composite_no_cost", "benchmark"], 2, false);
    expect(둘다).toContain("이 결과를 읽기 전에");
    expect(둘다).toContain("종합 점수 (비용 0)");
    expect(elsewhereNote(["core_h", "core_b"], 0, false)).toBeNull();
    expect(elsewhereNote(["core_h"], 1, false)).not.toContain("비용 0");
    expect(elsewhereNote(["composite", "composite_no_cost"], 0, false)).not.toContain("읽기 전에");
    expect(elsewhereNote(["composite", "composite_no_cost"], 3, true)).toContain("다른 실행");
    // 25.1943 — 물러났는데 결과 표가 없으면 없음, composite 만 있고 비용 0 사본이 없는 묶음(장기 문턱 비교)은 비용 문장 없음
    expect(elsewhereNote([], 0, true)).toBeNull();
    expect(elsewhereNote(["composite", "benchmark"], 0, false)).toBeNull();
  });
});
