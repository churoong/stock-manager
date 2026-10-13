/** 텔레그램 "알림" 은 시장마다 제 마지막 거래일 (docs/infra.md 25.1253) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import type { ResultSet, SqlValue } from "@/lib/db";
import { answer, type Exec } from "@/lib/telegramQa";

function memExec(): { db: DatabaseSync; exec: Exec } {
  const db = new DatabaseSync(":memory:");
  const dir = join(process.cwd(), "..", "migrations");
  for (const f of readdirSync(dir)
    .filter((x) => x.endsWith(".sql"))
    .sort())
    db.exec(readFileSync(join(dir, f), "utf-8"));
  const exec: Exec = async (sql: string, args: SqlValue[] = []) => {
    const st = db.prepare(sql);
    const rows = st.all(...(args as Array<string | number | null>)) as Array<
      Record<string, unknown>
    >;
    const columns = st.columns().map((c) => c.name);
    return {
      columns,
      rows: rows.map((r) => columns.map((c) => r[c])),
      affectedRows: 0,
    } satisfies ResultSet;
  };
  return { db, exec };
}

describe("알림 답", () => {
  it("한국 낮에 물어도 미국의 지난 정규장 알림이 함께 나온다", async () => {
    const { db, exec } = memExec();
    db.exec(`INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
             VALUES (1, '000001', 'KOSPI', 'KR', '가나', 'KRW', 'active', 't', 't'),
                    (2, 'AAA', 'NASDAQ', 'US', '에이', 'USD', 'active', 't', 't')`);
    db.exec(`INSERT INTO alerts (stock_id, market, trade_date, trigger_type, message, data, created_at) VALUES
             (1, 'KR', '2026-10-13', 'spike_up', '국내 급등', '{}', '2026-10-13T01:00:00Z'),
             (1, 'KR', '2026-10-12', 'volume', '어제 국내', '{}', '2026-10-12T01:00:00Z'),
             (2, 'US', '2026-10-12', 'target', '미국 목표가', '{}', '2026-10-12T18:00:00Z')`);
    const text = await answer({ kind: "alerts" }, exec);
    expect(text).toContain("장중 알림 국내 (2026-10-13, 최근 1건)");
    expect(text).toContain("장중 알림 미국 (2026-10-12, 최근 1건)");
    expect(text).toContain("미국 목표가");
    expect(text).not.toContain("어제 국내");
  });
});
