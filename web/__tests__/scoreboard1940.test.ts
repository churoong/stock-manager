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
  walk_positive: 3, walk_total: 4, bh_q: 0.0421, capacity: 0.00042, capacity_complete: true, stage01_verdict: "모름", stage01_note: "재료 없음: 수용량", months: 48, rebalances: 1000, buys: 120, control_blocks: 9, gate_blocks: 4, sells_scored: 80,
  missing: { gate_lower: "하한 없음 — 묶음 4개(block_bound 최소 미달)", half_second_cagr: "second 반 CAGR 표본 미달" },
};

describe("판정판 글 (25.1940)", () => {
  it("값은 꼴만 바꾼다 — 비율은 %, 분위 하한 소수 한 자리, DSR 두 자리와 N", () => {
    const l = scoreLine(parseScoreboard([줄]).rows[0]);
    expect(l.label).toBe("핵심 H (합의)");
    expect(l.cohort).toBe("코호트 1b");
    expect(Object.fromEntries(l.cells)).toEqual({
      CAGR: "+12.3%", "초과 CAGR": "-1.0%", MDD: "-25.7%", DSR: "0.93 (N 17)", "대조 하한": "51.0", "BH q": "0.042", "수용량": "0.042% / 1억 원", "관문 하한": "없음",
      "앞 반": "+5.0%", "뒤 반": "없음", "걷는 창": "3/4 창 초과 > 0",
    });
    // 25.1947 — 창이 없으면(12개월 미만) 0/0 이 아니라 그렇게, 한쪽이라도 비면 없음
    const 창 = (walk_positive: number | null, walk_total: number | null) =>
      Object.fromEntries(scoreLine(parseScoreboard([{ ...줄, walk_positive, walk_total }]).rows[0]).cells)["걷는 창"];
    expect(창(0, 0)).toBe("창 없음(12개월 미만)");
    expect(창(0, 4)).toBe("0/4 창 초과 > 0");
    expect(창(null, 4)).toBe("없음");
    expect(창(3, null)).toBe("없음");
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
    // 25.1952 — 나중에 생긴 칸은 생긴 판으로 까닭을 적는다
    expect(빈.missing).toEqual([
      "없음: CAGR — 까닭 기록 없음",
      "없음: 걷는 창·걷는 창 수 — 이 칸이 생기기(25.1947) 전 실행 — 다음 실행부터 찹니다",
      "없음: BH q — 이 칸이 생기기(25.1951) 전 실행 — 다음 실행부터 찹니다",
      "없음: 수용량 — 이 칸이 생기기(25.1954) 전 실행 — 다음 실행부터 찹니다",
      "없음: 수용량 하한 여부 — 이 칸이 생기기(25.1956) 전 실행 — 다음 실행부터 찹니다",
      "없음: 0→1 점검·0→1 근거 — 이 칸이 생기기(25.1950) 전 실행 — 다음 실행부터 찹니다",
    ]);
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
    // 25.1943 — 물러났는데 결과 표가 없으면 없음. composite 만 있고 비용 0 사본이 없는 꼴은 넓히기 변이(startsWith)를
    // 잡는 가상의 경계다(25.1944 — only_long 묶음은 composite 도 없이 benchmark·long_q* 만 낸다)
    expect(elsewhereNote([], 0, true)).toBeNull();
    expect(elsewhereNote(["composite", "benchmark"], 0, false)).toBeNull();
    // 실제 only_long 꼴도 없음(25.1946 — 주석을 맺고 단언 바로 위로)
    expect(elsewhereNote(["benchmark", "long_q60", "long_q70"], 0, false)).toBeNull();
    // 25.1944 — 물러난 문장은 경고·비용 0 줄을 가리키지 않는다(가리킬 곳이 있든 없든)
    for (const [s, w] of [[["composite", "composite_no_cost"], 2], [["core_h"], 0]] as const) {
      expect(elsewhereNote(s, w, true)).not.toMatch(/경고|비용 0/);
    }
  });

  it("화면 배선 — 결과 표의 전략·경고 띠 수·물러남이 그대로 안내 문장까지 간다(25.1946)", async () => {
    const { scoreboardNoteFor } = await import("@/lib/scoreboard");
    const 월 = [
      { strategy: "composite", warnings_json: '["생존편향"]' },
      { strategy: "composite_no_cost", warnings_json: "[]" },
    ];
    const 둘다 = scoreboardNoteFor({ runs: 월, scoreboard: { fallback: false } });
    expect(둘다).toContain("이 결과를 읽기 전에");
    expect(둘다).toContain("종합 점수 (비용 0)");
    // 경고가 없으면 경고 띠를 가리키지 않는다 — 못 읽은 경고는 띠에 한 줄로 나오므로 가리킨다(`mergeWarnings`)
    expect(scoreboardNoteFor({ runs: [{ strategy: "core_h", warnings_json: "[]" }], scoreboard: { fallback: false } })).toBeNull();
    expect(scoreboardNoteFor({ runs: [{ strategy: "core_h", warnings_json: "{" }], scoreboard: { fallback: false } })).toContain("읽기 전에");
    expect(scoreboardNoteFor({ runs: 월, scoreboard: { fallback: true } })).toContain("다른 실행");
    expect(scoreboardNoteFor({ runs: 월, scoreboard: null })).toBeNull();
    expect(scoreboardNoteFor(null)).toBeNull();
    // 화면이 이 함수를 쓴다 — 예전처럼 인자를 직접 넘기면 이 시험이 배선을 못 본다
    const { readFileSync } = await import("node:fs");
    const 화면 = readFileSync("components/BacktestView.tsx", "utf-8");
    expect(화면).toContain("note={scoreboardNoteFor(data)}");
    expect(화면).not.toContain("elsewhereNote(");
  });

  it("0→1 점검 한 줄 — 배치가 낸 판정과 근거를 그대로, 시도 아닌 줄은 없음(25.1950)", () => {
    const 줄1 = scoreLine(parseScoreboard([{ ...줄, stage01_verdict: "탈락", stage01_note: "걸림: DSR · 재료 없음: 수용량" }]).rows[0]);
    expect(줄1.stage).toBe("0→1(그림자로) 점검: 탈락 — 걸림: DSR · 재료 없음: 수용량");
    expect(scoreLine(parseScoreboard([{ ...줄, stage01_verdict: "모름", stage01_note: null }]).rows[0]).stage).toBe("0→1(그림자로) 점검: 모름");
    const 기준줄 = scoreLine(parseScoreboard([{ ...줄, strategy: "benchmark", stage01_verdict: null, stage01_note: null,
      missing: { stage01_verdict: "기준·무작위 대조 — 시도 아님", stage01_note: "기준·무작위 대조 — 시도 아님" } }]).rows[0]);
    expect(기준줄.stage).toBeNull();
    expect(기준줄.missing).toContain("없음: 0→1 점검·0→1 근거 — 기준·무작위 대조 — 시도 아님");
  });

  it("새 칸이 생기기 전 실행의 줄 — 키도 까닭도 없으면 생긴 판으로 까닭을 적는다(25.1952)", () => {
    const 옛 = { ...줄 } as Record<string, unknown>;
    for (const k of ["walk_positive", "walk_total", "bh_q", "stage01_verdict", "stage01_note"]) delete 옛[k];
    const l = scoreLine(parseScoreboard([옛]).rows[0]);
    expect(Object.fromEntries(l.cells)["걷는 창"]).toBe("없음");
    expect(l.missing).toContain("없음: 걷는 창·걷는 창 수 — 이 칸이 생기기(25.1947) 전 실행 — 다음 실행부터 찹니다");
    expect(l.missing).toContain("없음: BH q — 이 칸이 생기기(25.1951) 전 실행 — 다음 실행부터 찹니다");
    expect(l.missing).toContain("없음: 0→1 점검·0→1 근거 — 이 칸이 생기기(25.1950) 전 실행 — 다음 실행부터 찹니다");
    expect(l.stage).toBeNull();
    // 배치 까닭이 있으면 그것이 먼저 — 값이 있으면 까닭 없음
    const 새 = scoreLine(parseScoreboard([{ ...줄, bh_q: null, missing: { ...줄.missing, bh_q: "코호트 밖 — BH 묶음 없음" } }]).rows[0]);
    expect(새.missing).toContain("없음: BH q — 코호트 밖 — BH 묶음 없음");
    expect(새.missing.join()).not.toContain("생기기");
  });

  it("수용량 칸 — 단위 금액은 시장마다, 모르는 시장은 '단위'(25.1954)", () => {
    const 칸 = (market: string | null, capacity: number | null) =>
      Object.fromEntries(scoreLine(parseScoreboard([{ ...줄, market, capacity }]).rows[0]).cells)["수용량"];
    expect(칸("US", 0.0123)).toBe("1.230% / 10만 달러");
    expect(칸("KR", null)).toBe("없음");
    expect(칸(null, 0.001)).toBe("0.100% / 단위");
    // 25.1956 — 재지 못한 날이 있으면 하한(≥), 아주 작으면 "< 0.001%", BH q 도 작으면 "< 0.001"
    const 하한 = Object.fromEntries(scoreLine(parseScoreboard([{ ...줄, capacity: 0.004, capacity_complete: false }]).rows[0]).cells);
    expect(하한["수용량"]).toBe("≥ 0.400% / 1억 원");
    expect(칸("US", 5e-7)).toBe("< 0.001% / 10만 달러");
    // 25.1958 — 하한이면서 작은 값은 "≥ < 0.001%" 가 아니라 "≥ 0.000%"
    const 작은하한 = Object.fromEntries(scoreLine(parseScoreboard([{ ...줄, market: "US", capacity: 5e-7, capacity_complete: false }]).rows[0]).cells);
    expect(작은하한["수용량"]).toBe("≥ 0.000% / 10만 달러");
    expect(Object.fromEntries(scoreLine(parseScoreboard([{ ...줄, bh_q: 0.0002 }]).rows[0]).cells)["BH q"]).toBe("< 0.001");
  });
});
