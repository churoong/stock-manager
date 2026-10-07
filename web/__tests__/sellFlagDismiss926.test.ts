/** 매도 플래그 확인: 지난날(비활성) 번호로 누르면 성공처럼 굴지 않는다 (docs/infra.md 25.926, 감사). 실제 스키마로 돈다. */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, describe, expect, it, vi } from "vitest";

function 새디비() {
  const d = new DatabaseSync(":memory:");
  for (const f of readdirSync(join(process.cwd(), "..", "migrations")).filter((x) => x.endsWith(".sql")).sort()) {
    d.exec(readFileSync(join(process.cwd(), "..", "migrations", f), "utf-8"));
  }
  d.exec(
    "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)" +
      " VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')",
  );
  const 넣기 = d.prepare(
    "INSERT INTO sell_flags (id, stock_id, as_of_date, level, reason_code, rationale_text, rationale_data, is_active," +
      " first_seen_date, dismissed_at, created_at) VALUES (?, 1, ?, 'red', 'stop', 't', '{}', ?, '2026-10-02', NULL, 't')",
  );
  넣기.run(41, "2026-10-02", 0); // 어제 행 — 오늘 아침 실행이 비활성으로 돌렸다
  넣기.run(57, "2026-10-05", 1); // 오늘 행
  return d;
}

function 연결(d: DatabaseSync) {
  vi.doMock("@/lib/db", () => ({
    execute: async (sql: string, args: unknown[] = []) => {
      const st = d.prepare(sql);
      if (sql.trimStart().startsWith("UPDATE")) {
        const r = st.run(...(args as never[]));
        return { columns: [], rows: [], affectedRows: Number(r.changes) };
      }
      const rows = st.all(...(args as never[])) as Record<string, unknown>[];
      return { columns: rows[0] ? Object.keys(rows[0]) : [], rows: rows.map((x) => Object.values(x)), affectedRows: 0 };
    },
  }));
}

describe("매도 플래그 확인 — 비활성 행", () => {
  afterEach(() => {
    vi.doUnmock("@/lib/db");
    vi.resetModules();
  });

  const 부르기 = async (id: number) => {
    const { POST } = await import("@/app/api/sell-flags/[id]/route");
    return POST(new Request("http://x", { method: "POST" }), { params: Promise.resolve({ id: String(id) }) });
  };

  it("어제 번호로 누르면 409 이고 아무 행도 찍지 않는다", async () => {
    vi.resetModules();
    const d = 새디비();
    연결(d);
    const r = await 부르기(41);
    expect(r.status).toBe(409);
    const 찍힘 = d.prepare("SELECT COUNT(*) AS n FROM sell_flags WHERE dismissed_at IS NOT NULL").get() as { n: number };
    expect(찍힘.n).toBe(0);
  });

  it("오늘 번호는 확인되고, 다시 누르면 이미 확인됨", async () => {
    vi.resetModules();
    const d = 새디비();
    연결(d);
    expect((await 부르기(57)).status).toBe(200);
    vi.resetModules();
    const 두번째 = await 부르기(57);
    expect(두번째.status).toBe(200);
    expect(((await 두번째.json()) as { already?: boolean }).already).toBe(true);
  });
});
