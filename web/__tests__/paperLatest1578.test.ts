/**
 * 위원회 감사 [권고] 4·5 (docs/infra.md 25.1578) — 가상모의투자 화면의 최신 행 질의가 표를 훑지 않고,
 * "왜 안 샀나" 가 위원회 계좌에서도 같은 종목·같은 까닭을 묶고 그 뒤 성과가 찬 줄을 보인다.
 * 실제 마이그레이션을 적용한 SQLite 에 돌린다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeEach, describe, expect, it } from "vitest";
import { PAPER_DOSSIERS_LATEST, PAPER_LATEST_DAILY, PAPER_MEETINGS_LATEST } from "@/lib/paper";
import { DECISIONS_DONE_SQL, DECISIONS_SQL, doneWindow, groupDecisions, mergeDecisions, reasonKey, type DecisionRow } from "@/lib/paperDecisions";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;
const 계획 = (sql: string, ...args: string[]) =>
  (db.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...args) as Array<{ detail: string }>).map((p) => p.detail).join(" | ");

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
  db.exec("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at) VALUES (1, '005930', 'KOSPI', 'KR', '가', 'KRW', 'active', 't', 't')");
  db.exec("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at) VALUES (2, '000660', 'KOSPI', 'KR', '나', 'KRW', 'active', 't', 't')");
});

describe("최신 행 질의 — 계좌·시장마다 한 행만 찾는다", () => {
  it("결과는 예전과 같다", () => {
    const 일 = db.prepare("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw, pnl_krw, twr_index, positions_json, recon_ok, updated_at) VALUES (?, ?, 1, 1, 0, 1, 0, 1, '[]', 1, 't')");
    for (const [a, d] of [["A", "2026-10-15"], ["A", "2026-10-16"], ["B", "2026-10-14"]]) 일.run(a, d);
    const 일행 = db.prepare(PAPER_LATEST_DAILY).all() as Array<{ account_id: string; as_of: string }>;
    expect(일행.map((r) => [r.account_id, r.as_of]).sort()).toEqual([["A", "2026-10-16"], ["B", "2026-10-14"]]);
    const 회 = db.prepare("INSERT INTO paper_meetings (account_id, market, decided_on, charter_hash, minutes_json, created_at) VALUES (?, ?, ?, 'h', '{}', 't')");
    for (const [a, m, d] of [["A", "KR", "2026-10-15"], ["A", "KR", "2026-10-16"], ["A", "US", "2026-10-14"], ["C", "KR", "2026-10-13"]]) 회.run(a, m, d);
    const 회행 = db.prepare(PAPER_MEETINGS_LATEST).all() as Array<{ account_id: string; market: string; decided_on: string }>;
    expect(회행.map((r) => [r.account_id, r.market, r.decided_on]).sort()).toEqual([
      ["A", "KR", "2026-10-16"], ["A", "US", "2026-10-14"], ["C", "KR", "2026-10-13"],
    ]);
    const 조 = db.prepare("INSERT INTO paper_dossiers (market, as_of, stock_id, dossier_json, created_at) VALUES (?, ?, ?, '{}', 't')");
    for (const [m, d, s] of [["KR", "2026-10-15", 1], ["KR", "2026-10-16", 1], ["KR", "2026-10-16", 2]] as const) 조.run(m, d, s);
    const 조행 = db.prepare(PAPER_DOSSIERS_LATEST).all() as Array<{ as_of: string; stock_id: number }>;
    expect(조행.map((r) => [r.as_of, r.stock_id]).sort()).toEqual([["2026-10-16", 1], ["2026-10-16", 2]]);
  });

  it("질의 계획 — 일별·회의록·조사 자료 표를 훑지 않는다", () => {
    for (const [sql, 표] of [[PAPER_LATEST_DAILY, "d"], [PAPER_MEETINGS_LATEST, "m"], [PAPER_DOSSIERS_LATEST, "d"]] as const) {
      const p = 계획(sql);
      expect(p, p).not.toMatch(new RegExp(`SCAN ${표}(?! USING)`));
      expect(p, p).not.toMatch(/SCAN x(?! USING)/);
      expect(p, p).toMatch(new RegExp(`SEARCH ${표} USING`));
    }
  });
});

describe("왜 안 샀나 — 위원회 계좌", () => {
  const 줄 = (id: number, day: string, reason: string, ret5: number | null = null): DecisionRow => ({
    id, decided_on: day, kind: "skip", reason, ticker: "005930", name: "가",
    ret_5d: ret5, ret_20d: null, ret_60d: null, bench_5d: null, bench_20d: null, bench_60d: null, outcome_note: null,
  });

  it("까닭의 수만 다르면 묶는다 — 보이는 까닭은 최근 줄", () => {
    expect(reasonKey("위원회: 표 0.83 < 문턱 1")).toBe(reasonKey("위원회: 표 -1.2 < 문턱 1"));
    expect(reasonKey("위원회: 표 0.83 < 문턱 1")).not.toBe(reasonKey("점수 미달"));
    const g = groupDecisions([줄(2, "2026-10-16", "위원회: 표 0.5 < 문턱 1"), 줄(1, "2026-10-15", "위원회: 표 0.83 < 문턱 1")]);
    expect(g).toHaveLength(1);
    expect([g[0].count, g[0].first.id, g[0].last.reason]).toEqual([2, 1, "위원회: 표 0.5 < 문턱 1"]);
  });

  it("성과가 찬 줄도 싣는다 — 하루 수십 줄이라 최근 60줄엔 찬 줄이 없었다", () => {
    const ins = db.prepare(
      "INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ret_5d, created_at) VALUES ('A', 'KR', ?, ?, 'skip', ?, ?, 't')",
    );
    ins.run("2026-10-01", 2, "위원회: 표 0.4 < 문턱 1", 2.5);
    const 날 = (i: number) => new Date(Date.UTC(2026, 9, 2) + i * 86_400_000).toISOString().slice(0, 10);
    for (let i = 0; i < 70; i++) ins.run(날(i), 1, `위원회: 표 0.${i} < 문턱 1`, null);
    const 최근 = db.prepare(DECISIONS_SQL).all("A", "KR") as unknown as DecisionRow[];
    const 찬것 = db.prepare(DECISIONS_DONE_SQL).all("A", "KR", ...doneWindow("2026-12-10")) as unknown as DecisionRow[];
    expect(최근.some((r) => r.ret_5d != null)).toBe(false);
    expect(찬것.map((r) => r.ret_5d)).toEqual([2.5]);
    const 합 = mergeDecisions(최근, 찬것, 찬것);
    expect(합).toHaveLength(61);
    expect(합[합.length - 1].decided_on).toBe("2026-10-01");
    // 결정일 범위를 색인으로 건너뛴다 — 7일 안(못 찬 줄)과 120일 밖은 읽지 않는다 (25.1580)
    expect(계획(DECISIONS_DONE_SQL, "A", "KR", "x", "y")).toMatch(/USING INDEX sqlite_autoindex_paper_decisions_1 \(account_id=\? AND market=\? AND decided_on>\? AND decided_on<\?\)/);
    expect(doneWindow("2026-10-20")).toEqual(["2026-10-13", "2026-06-22"]);
    expect(db.prepare(DECISIONS_DONE_SQL).all("A", "KR", ...doneWindow("2026-10-07")).length).toBe(0); // 결정 뒤 7일 전
    expect(db.prepare(DECISIONS_DONE_SQL).all("A", "KR", ...doneWindow("2027-02-01")).length).toBe(0); // 120일 밖
  });

  it("경로가 두 질의를 다 읽는다", () => {
    const src = readFileSync(join(process.cwd(), "app", "api", "paper", "decisions", "route.ts"), "utf8");
    expect(src).toMatch(/execute\(DECISIONS_SQL, \[account, market\]\)/);
    expect(src).toMatch(/execute\(DECISIONS_DONE_SQL, \[account, market, \.\.\.doneWindow\(kstToday\(\)\)\]\)/);
  });
});
