/** 영문 정식 이름으로 물으면 그 종목 하나로 답한다 (docs/infra.md 25.1241) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import type { ResultSet, SqlValue } from "@/lib/db";
import { answer, type Exec } from "@/lib/telegramQa";

function memExec() {
  const db = new DatabaseSync(":memory:");
  const MIG = join(process.cwd(), "..", "migrations");
  for (const f of readdirSync(MIG).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIG, f), "utf-8"));
  const exec: Exec = async (sql: string, args: SqlValue[] = []) => {
    const st = db.prepare(sql);
    const rows = st.all(...(args as Array<string | number | null>)) as Array<Record<string, unknown>>;
    const columns = st.columns().map((c) => c.name);
    return { columns, rows: rows.map((r) => columns.map((c) => r[c])), affectedRows: 0 } as unknown as ResultSet;
  };
  return { db, exec };
}

describe("영문 이름이 정확히 같을 때", () => {
  it("앞부분이 같은 다른 종목이 있어도 하나로 답한다", async () => {
    const { db, exec } = memExec();
    db.exec(`INSERT INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at) VALUES
      (1,'005380','KOSPI','KR','현대차','Hyundai Motor','KRW','active','t','t'),
      (2,'001500','KOSPI','KR','현대차증권','Hyundai Motor Securities','KRW','active','t','t')`);
    expect((await answer({ kind: "stock", q: "hyundai motor" }, exec)).split("\n")[0]).toBe("현대차 (005380)");
    expect(await answer({ kind: "stock", q: "Hyundai" }, exec)).toContain("여럿이 맞습니다");
  });
});
