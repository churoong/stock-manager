/**
 * 종목 상세 신호 칸 — 건너뛴 신호 실행과 점수·신호 기준일 차이 (docs/infra.md 25.807, 종목 상세 감사).
 * 추천 화면은 "신호 계산을 건너뛰었습니다" 를 말하는데(25.798) 상세는 옛 기준일 신호를 말없이 보였다.
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { type Exec, loadSection, signalScoreDateNote } from "@/lib/stockDetail";

const MIGRATIONS = join(process.cwd(), "..", "migrations");

function 새DB() {
  const db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIGRATIONS, f), "utf-8"));
  db.exec(`
    INSERT INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at)
    VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', NULL, 'KRW', 'active', 't', 't');
    INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency, tranche_plan, target_price,
      stop_price, size_reduction, rationale_text, rationale_data, calc_version, created_at)
    VALUES (1, '2026-09-28', 'long', '밸류에이션 밴드', 1, 2, 'KRW', '[]', 3, 0.5, 1, '장기', '{"criteria": []}', 1, 't');
    INSERT INTO batch_runs (job_name, market, status, started_at, finished_at, trade_date)
    VALUES ('signals', 'KR', 'success', '2026-09-28T10:00:00Z', '2026-09-28T10:05:00Z', '2026-09-28');
  `);
  const exec: Exec = async (sql, args = []) => db.prepare(sql).all(...(args as Array<string | number>)) as Record<string, unknown>[];
  return { db, exec };
}

describe("상세 신호 칸의 건너뜀 안내", () => {
  it("최근 실행이 새 날짜를 건너뛰었으면 말한다", async () => {
    const { db, exec } = 새DB();
    db.exec(`INSERT INTO batch_runs (job_name, market, status, started_at, trade_date, step_log)
      VALUES ('signals', 'KR', 'skipped', '2026-09-29T10:00:00Z', '2026-09-29', '{"reason": "시세 일부 실패"}')`);
    const r = await loadSection(exec, "signals", 1, { country: "KR", today: "2026-09-29" });
    expect(r.run_note).toContain("2026-09-29 기준 신호 계산을 건너뛰었습니다 (시세 일부 실패)");
    expect(r.run_note).toContain("아래는 2026-09-28 기준");
  });

  it("최근 실행이 성공이면 말하지 않는다", async () => {
    const { exec } = 새DB();
    const r = await loadSection(exec, "signals", 1, { country: "KR", today: "2026-09-29" });
    expect(r.run_note).toBeNull();
  });

  it("신호가 없는 날은 사유 줄 앞에 붙는다 — 빈 칸도 건너뜀을 말한다", async () => {
    const { db, exec } = 새DB();
    // 다른 종목에는 신호가 있어 기준일(9/28)은 있고, 이 종목만 신호가 없다
    db.exec(`INSERT INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at)
      VALUES (2, '000660', 'KOSPI', 'KR', 'SK하이닉스', NULL, 'KRW', 'active', 't', 't');
      UPDATE signals SET stock_id = 2; INSERT INTO batch_runs (job_name, market, status, started_at, trade_date, error_text)
      VALUES ('signals', 'KR', 'failed', '2026-09-29T10:00:00Z', '2026-09-29', 'HTTP 503')`);
    const r = await loadSection(exec, "signals", 1, { country: "KR", today: "2026-09-29" });
    expect(String(r.reason)).toContain("실패했습니다 (HTTP 503)");
    expect(String(r.reason)).toContain("2026-09-28 기준 신호 없음");
  });
});

describe("점수·신호 기준일 차이", () => {
  it("다르면 두 날짜를 말하고, 같거나 하나가 없으면 조용하다", () => {
    expect(signalScoreDateNote("2026-09-28", "2026-09-29")).toContain("신호는 2026-09-28 기준, 위 점수는 2026-09-29 기준");
    expect(signalScoreDateNote("2026-09-29", "2026-09-29")).toBeNull();
    expect(signalScoreDateNote(null, "2026-09-29")).toBeNull();
    // 점수가 더 묵은 것은 점수 칸이 말한다 (25.812)
    expect(signalScoreDateNote("2026-09-29", "2026-09-20")).toBeNull();
  });

  it("추천 화면과 상세가 같은 성공 실행 질의를 쓴다", () => {
    const 경로 = readFileSync("app/api/recommend/route.ts", "utf-8");
    expect(경로).toContain("execute(LAST_SIGNAL_SUCCESS, [parsed.data.country])");
    const 화면 = readFileSync("components/StockDetail.tsx", "utf-8");
    expect(화면).toContain("runNote={(data.run_note");
  });
});
