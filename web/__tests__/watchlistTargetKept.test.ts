import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * **관심 버튼이 저장해 둔 목표 매수가를 지우지 않는가** (docs/infra.md 25.251).
 *
 * `lib/watch.addWatch` 는 `{stock_id}` 만 보낸다. 예전 upsert 는 `target_buy_price = excluded.target_buy_price` 라
 * 이미 관심인 종목에 다시 누르면 목표가가 NULL 이 되고 장중 매수 구간 알림이 멈췄다. 실제 스키마 SQLite 로 경로를 돌린다.
 */

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS)
    .filter((f) => f.endsWith(".sql"))
    .sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  db.exec(`INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
    VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')`);
  vi.resetModules();
  vi.doMock("@/lib/db", async (orig) => {
    const 진짜 = await (orig as () => Promise<Record<string, unknown>>)();
    return {
      ...진짜,
      execute: async (sql: string, args: unknown[] = []) => {
        // 읽기는 행을 돌려준다 — 종목 존재 확인(25.814)이 읽는다
        if (/^\s*SELECT/i.test(sql)) {
          const rows = db.prepare(sql).all(...(args as never[])) as Record<string, unknown>[];
          return { columns: Object.keys(rows[0] ?? {}), rows: rows.map((r) => Object.values(r)), affectedRows: 0 };
        }
        const r = db.prepare(sql).run(...(args as never[]));
        return { columns: [], rows: [], affectedRows: Number(r.changes) };
      },
    };
  });
});

afterEach(() => {
  vi.doUnmock("@/lib/db");
});

async function 보내기(body: object) {
  const { POST } = await import("@/app/api/watchlist/route");
  const res = await POST(
    new Request("https://x/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
  expect(res.status).toBe(200);
}

const 목표가 = () =>
  (
    db
      .prepare("SELECT target_buy_price AS v FROM watchlist WHERE stock_id = 1")
      .get() as { v: number | null }
  ).v;

describe("관심 종목 목표 매수가", () => {
  it("목표가 없이 다시 추가해도 저장해 둔 목표가를 지키고", async () => {
    await 보내기({ stock_id: 1, target_buy_price: 70000 });
    await 보내기({ stock_id: 1 }); // 추천 목록의 관심 버튼
    expect(목표가()).toBe(70000);
  });

  it("명시적으로 비우면 비운다", async () => {
    await 보내기({ stock_id: 1, target_buy_price: 70000 });
    await 보내기({ stock_id: 1, target_buy_price: null });
    expect(목표가()).toBeNull();
  });
});


describe("관심 종목 쓰기의 남은 하 (docs/infra.md 25.814, 관심·설정 감사)", () => {
  it("없는 종목 번호는 400 — 고아 행을 만들지 않는다", async () => {
    const { POST } = await import("@/app/api/watchlist/route");
    const res = await POST(new Request("https://x/api/watchlist", { method: "POST", body: JSON.stringify({ stock_id: 999 }) }));
    expect(res.status).toBe(400);
    expect(db.prepare("SELECT COUNT(*) AS n FROM watchlist").get()).toEqual({ n: 0 });
  });

  it("다시 등록하면 꺼 둔 알림을 켠다", async () => {
    await 보내기({ stock_id: 1 });
    db.exec("UPDATE watchlist SET alert_enabled = 0");
    await 보내기({ stock_id: 1 });
    expect(db.prepare("SELECT alert_enabled AS a FROM watchlist").get()).toEqual({ a: 1 });
    // 목표가를 고치려고 "추가" 폼을 쓴 것은 꺼 둔 알림을 켜지 않는다 (25.817, 교차검증)
    db.exec("UPDATE watchlist SET alert_enabled = 0");
    await 보내기({ stock_id: 1, target_buy_price: 50_000 });
    expect(db.prepare("SELECT alert_enabled AS a, target_buy_price AS t FROM watchlist").get()).toEqual({ a: 0, t: 50_000 });
  });

  it("없는 번호를 고치거나 지우면 404", async () => {
    const { PATCH, DELETE } = await import("@/app/api/watchlist/[id]/route");
    const p = await PATCH(new Request("https://x/api/watchlist/77", { method: "PATCH", body: JSON.stringify({ alert_enabled: false }) }),
      { params: Promise.resolve({ id: "77" }) });
    expect(p.status).toBe(404);
    const d = await DELETE(new Request("https://x/api/watchlist/77", { method: "DELETE" }), { params: Promise.resolve({ id: "77" }) });
    expect(d.status).toBe(404);
  });

  it("장중 감시는 상장폐지된 관심 종목을 보지 않는다", () => {
    expect(readFileSync("app/api/cron/intraday/route.ts", "utf-8")).toContain("s.yahoo_symbol IS NOT NULL AND s.status <> 'delisted'");
  });
});
