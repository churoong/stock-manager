/** 넣은 원금보다 큰 배당은 묻고, 확인하면 저장한다 (docs/infra.md 25.934, 감사). 실제 스키마로 돈다. */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, describe, expect, it, vi } from "vitest";

function 새디비() {
  const d = new DatabaseSync(":memory:");
  const 폴더 = join(process.cwd(), "..", "migrations");
  for (const f of readdirSync(폴더).filter((x) => x.endsWith(".sql")).sort()) d.exec(readFileSync(join(폴더, f), "utf-8"));
  d.exec(`
    INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
    VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't');
    INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, fee, created_at, updated_at)
    VALUES (1, 'buy', '2026-03-02', 55000, 1, 'KRW', 1, 'none', 0, 't', 't');
  `);
  return d;
}

function 연결(d: DatabaseSync) {
  vi.doMock("@/lib/db", () => ({
    execute: async (sql: string, args: unknown[] = []) => {
      const st = d.prepare(sql);
      if (/^\s*(INSERT|UPDATE|DELETE)/i.test(sql)) {
        const r = st.run(...(args as never[]));
        return { columns: [], rows: [], affectedRows: Number(r.changes) };
      }
      const rows = st.all(...(args as never[])) as Record<string, unknown>[];
      return { columns: rows[0] ? Object.keys(rows[0]) : [], rows: rows.map((x) => Object.values(x)), affectedRows: 0 };
    },
    rowsToObjects: (rs: { columns: string[]; rows: unknown[][] }) =>
      rs.rows.map((r) => Object.fromEntries(rs.columns.map((c, i) => [c, r[i]]))),
  }));
  vi.doMock("@/lib/portfolio", async () => ({
    ...(await vi.importActual<object>("@/lib/portfolio")),
    requestRecalc: async () => ({ requested: false }),
  }));
}

const 보내기 = async (body: object) => {
  const { POST } = await import("@/app/api/dividends/route");
  return POST(new Request("http://x", { method: "POST", body: JSON.stringify(body) }));
};

describe("배당 크기 확인", () => {
  afterEach(() => {
    vi.doUnmock("@/lib/db");
    vi.doUnmock("@/lib/portfolio");
    vi.resetModules();
  });

  // 기록 전부터 500주를 들고 있다가 1주만 새로 적은 사용자의 진짜 배당 — 원금(55,000)보다 크다
  const 배당 = { stock_id: 1, pay_date: "2026-04-15", gross_amount: 182_305, tax: 0 };

  it("확인 없이는 409 로 묻고 저장하지 않는다", async () => {
    vi.resetModules();
    const d = 새디비();
    연결(d);
    const r = await 보내기(배당);
    expect(r.status).toBe(409);
    expect(((await r.json()) as { size_unit?: boolean }).size_unit).toBe(true);
    expect((d.prepare("SELECT COUNT(*) AS n FROM dividend_receipts").get() as { n: number }).n).toBe(0);
  });

  it("확인하면 경고와 함께 저장한다", async () => {
    vi.resetModules();
    const d = 새디비();
    연결(d);
    const r = await 보내기({ ...배당, confirm_size: true });
    expect(r.status).toBe(200);
    expect(((await r.json()) as { warnings: string[] }).warnings.join(" ")).toContain("원금");
    expect((d.prepare("SELECT COUNT(*) AS n FROM dividend_receipts").get() as { n: number }).n).toBe(1);
  });
});
