import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * **조용시간 해제 호출이 밤새 쌓인 알림을 다 보내는가** (docs/infra.md 25.253).
 *
 * 07:00 해제 호출은 장 밖이라 한 번뿐인데, 예전에는 50건 한 통만 보내고 끝났다. 나머지는 다음 호출
 * (평일 09:00, 주말은 더 늦게)까지 남았다. 실제 스키마 SQLite 로 경로를 돌리고 텔레그램만 가로챈다.
 */

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;
const 보낸글: string[] = [];
const 보낸때: number[] = [];

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS)
    .filter((f) => f.endsWith(".sql"))
    .sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  db.exec(`INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at)
    VALUES (1, 'AAPL', 'NASDAQ', 'US', 'Apple', 'USD', 'active', 't', 't')`);
  const 넣기 = db.prepare(
    "INSERT INTO alerts (stock_id, market, trade_date, trigger_type, message, data, created_at) VALUES (1, 'US', ?, ?, ?, '{}', ?)",
  );
  for (let i = 0; i < 120; i++) {
    넣기.run(
      `2026-10-${String(1 + (i % 28)).padStart(2, "0")}`,
      `t${i}`,
      `알림 ${i}`,
      `2026-10-20T1${i % 10}:00:00Z`,
    );
  }
  보낸글.length = 0;
  process.env.CRON_SECRET = "secret";
  vi.resetModules();
  const 돌리기 = async (sql: string, args: unknown[] = []) => {
    const stmt = db.prepare(sql);
    if (!/^\s*(SELECT|WITH)/i.test(sql)) {
      const r = stmt.run(...(args as never[]));
      return { columns: [], rows: [], affectedRows: Number(r.changes) };
    }
    const rows = stmt.all(...(args as never[])) as Array<
      Record<string, unknown>
    >;
    const columns = rows[0]
      ? Object.keys(rows[0])
      : stmt.columns().map((c) => c.name);
    return {
      columns,
      rows: rows.map((r) => columns.map((c) => r[c])),
      affectedRows: 0,
    };
  };
  vi.doMock("@/lib/db", async (orig) => ({
    ...(await (orig as () => Promise<Record<string, unknown>>)()),
    execute: 돌리기,
    batch: async (items: Array<{ sql: string; args: unknown[] }>) =>
      Promise.all(items.map((i) => 돌리기(i.sql, i.args))),
  }));
  vi.doMock("@/lib/telegram", async (orig) => ({
    ...(await (orig as () => Promise<Record<string, unknown>>)()),
    sendTelegram: async (text: string) => {
      보낸글.push(text);
      보낸때.push(performance.now());
      return [1];
    },
  }));
  vi.useFakeTimers({ now: new Date("2026-10-20T22:01:00Z"), toFake: ["Date"] }); // 07:01 KST
});

afterEach(() => {
  vi.doUnmock("@/lib/db");
  vi.doUnmock("@/lib/telegram");
  vi.useRealTimers();
});

describe("조용시간 해제", () => {
  it("밤새 쌓인 120건을 한 호출에 모두 보낸다", async () => {
    const { GET } = await import("@/app/api/cron/intraday/route");
    const res = await GET(
      new Request("https://x/api/cron/intraday?market=US", {
        headers: { "x-cron-secret": "secret" },
      }),
    );
    const body = (await res.json()) as { flushed: number };
    expect(body.flushed).toBe(120);
    expect(보낸글.length).toBe(3); // 50 · 50 · 20
    // 통 사이를 띄운다 — 쉬지 않고 보내면 대화당 초당 한도에 닿는다 (docs/infra.md 25.347)
    for (let i = 1; i < 보낸때.length; i++)
      expect(보낸때[i] - 보낸때[i - 1]).toBeGreaterThanOrEqual(1_000);
    const 남은 = db
      .prepare("SELECT COUNT(*) AS n FROM alerts WHERE sent_at IS NULL")
      .get() as { n: number };
    expect(남은.n).toBe(0);
  });
});
