/** 알림 센터 필터·읽음 (docs/intraday.md 8장). */
import { DatabaseSync } from "node:sqlite";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { DEFAULT_FILTER, filterAlerts, readUpdateSql, unreadCount } from "@/lib/alerts";

const rows = [
  { id: 1, market: "KR", trigger_type: "spike_up", is_read: 0 },
  { id: 2, market: "US", trigger_type: "target", is_read: 1 },
  { id: 3, market: "KR", trigger_type: "target", is_read: 0 },
];

describe("필터", () => {
  it("기본은 전부", () => {
    expect(filterAlerts(rows, DEFAULT_FILTER)).toHaveLength(3);
  });
  it("시장·트리거·안 읽음을 겹쳐 건다", () => {
    expect(filterAlerts(rows, { market: "KR", trigger: "ALL", unreadOnly: false }).map((r) => r.id)).toEqual([1, 3]);
    expect(filterAlerts(rows, { market: "ALL", trigger: "target", unreadOnly: true }).map((r) => r.id)).toEqual([3]);
    expect(unreadCount(rows)).toBe(2);
  });
});

describe("읽음 갱신", () => {
  it("ids 가 비면 안 읽은 전부, 있으면 그 id 만 (중복·이상한 값 제거)", () => {
    expect(readUpdateSql([], true)).toEqual({ sql: "UPDATE alerts SET is_read = 1 WHERE is_read = 0", args: [] });
    const q = readUpdateSql([3, 3, -1, 1.5, 7], true);
    expect(q.args).toEqual([3, 7]);
    expect(q.sql).toBe("UPDATE alerts SET is_read = 1 WHERE id IN (?, ?)");
  });

  it("실제 스키마에 is_read 가 있고 갱신이 된다", () => {
    const conn = new DatabaseSync(":memory:");
    const dir = join(process.cwd(), "..", "migrations");
    for (const f of readdirSync(dir).filter((x) => x.endsWith(".sql")).sort()) conn.exec(readFileSync(join(dir, f), "utf-8"));
    conn.exec("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at) VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')");
    conn.exec("INSERT INTO alerts (stock_id, market, trade_date, trigger_type, message, data, created_at) VALUES (1, 'KR', '2026-09-17', 'spike_up', 'm', '{}', 't'), (1, 'KR', '2026-09-17', 'target', 'm', '{}', 't')");
    const q = readUpdateSql([], true);
    conn.prepare(q.sql).run(...q.args);
    expect(conn.prepare("SELECT SUM(is_read) AS n FROM alerts").get()).toEqual({ n: 2 });
  });
});

describe("읽음 갱신을 나눠 보낸다 (docs/infra.md 25.5)", () => {
  it("D1 한도(질의당 파라미터 100개)를 넘지 않는다", async () => {
    const { readUpdateStatements, IDS_PER_STATEMENT } = await import("@/lib/alerts");
    const ids = Array.from({ length: 200 }, (_, i) => i + 1);
    const statements = readUpdateStatements(ids, true);
    expect(statements).toHaveLength(Math.ceil(200 / IDS_PER_STATEMENT));
    for (const s of statements) expect(s.args.length).toBeLessThan(100);
    expect(statements.flatMap((s) => s.args)).toEqual(ids);
  });

  it("비면 안 읽은 전부를 문장 하나로", async () => {
    const { readUpdateStatements } = await import("@/lib/alerts");
    expect(readUpdateStatements([], true)).toHaveLength(1);
  });
});
