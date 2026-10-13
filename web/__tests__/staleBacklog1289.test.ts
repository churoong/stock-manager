import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { sentLabel } from "@/lib/alerts";

// 며칠 묵은 대기 알림은 다시 켠 첫 호출에 한꺼번에 보내지 않는다 (docs/infra.md 25.1289, 장중 감사 재현)
describe("묵은 대기 알림", () => {
  const 글 = readFileSync("app/api/cron/intraday/route.ts", "utf8");
  const 문장 =
    /"(UPDATE alerts SET sent_at = \? WHERE sent_at IS NULL AND created_at < \?)"/.exec(
      글,
    )?.[1];

  it("경로가 보내기 전에 오래된 대기를 접는다", () => {
    expect(문장).toBeTruthy();
    expect(글).toContain("const PENDING_MAX_AGE_MS = 4 * 86_400_000;");
    expect(
      글.indexOf(
        "UPDATE alerts SET sent_at = ? WHERE sent_at IS NULL AND created_at < ?",
      ),
    ).toBeLessThan(글.indexOf("flushOnce(now,"));
  });

  it("그 문장은 오래된 대기만 바꾸고 색인을 탄다", () => {
    const db = new DatabaseSync(":memory:");
    const dir = join(process.cwd(), "..", "migrations");
    for (const f of readdirSync(dir)
      .filter((x) => x.endsWith(".sql"))
      .sort())
      db.exec(readFileSync(join(dir, f), "utf-8"));
    db.exec(
      `INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at) VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 't', 't')`,
    );
    db.exec(`INSERT INTO alerts (stock_id, market, trade_date, trigger_type, message, data, created_at, sent_at) VALUES
             (1, 'KR', '2026-10-09', 'after_hours', '옛', '{}', '2026-10-09T01:25:00Z', NULL),
             (1, 'KR', '2026-10-20', 'stop', '새', '{}', '2026-10-20T01:00:00Z', NULL)`);
    const r = db
      .prepare(문장!)
      .run("stale-skipped", "2026-10-16T01:00:00.000Z");
    expect(Number(r.changes)).toBe(1);
    expect(
      db.prepare("SELECT message FROM alerts WHERE sent_at IS NULL").all(),
    ).toEqual([{ message: "새" }]);
    expect(sentLabel("stale-skipped", (x) => x)).toContain("너무 오래 대기");
  });
});
