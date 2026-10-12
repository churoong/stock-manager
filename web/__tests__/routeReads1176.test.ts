import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { READ_FLUSH_ROWS, WEB_READS_JOB, noteRouteReads, resetRoutePending } from "@/lib/webUsage";
import { RECENT_HEALTH_ALERTS } from "@/lib/health";

/** 경로별 웹 읽기 (docs/infra.md 25.1176) — 무거운 읽기 문턱(20만)에 안 걸리는 작은 질의가 하루 1,500만 행을 읽었다 */
describe("경로별 웹 읽기", () => {
  beforeEach(() => resetRoutePending());
  const 스택 = "Error\n    at x (/var/task/.next/server/app/api/cron/health/route.js:1:1)";

  it("문턱 아래는 모으고, 넘으면 경로·모양별로 한 번에 적는다", async () => {
    const 보낸: Array<Array<{ sql: string; args: unknown[] }>> = [];
    const send = async (s: Array<{ sql: string; args: unknown[] }>) => { 보낸.push(s); };
    await noteRouteReads([{ sql: "SELECT job FROM health_alerts WHERE local_date >= ?" }], READ_FLUSH_ROWS - 1, new Date("2026-10-12T00:00:00Z"), send, 스택);
    expect(보낸).toHaveLength(0);
    await noteRouteReads([{ sql: "SELECT job FROM health_alerts WHERE local_date >= ?" }], 5, new Date("2026-10-12T00:00:00Z"), send, 스택);
    expect(보낸).toHaveLength(1);
    const [문장] = 보낸[0];
    expect(문장.args[0]).toBe(WEB_READS_JOB);
    expect(String(문장.args[2])).toContain("app/api/cron/health/route");
    expect(JSON.parse(String(문장.args[4]))).toEqual({ rows: READ_FLUSH_ROWS + 4, count: 2 });
  });

  it("못 적으면 되돌려 두었다가 다음 문턱에 함께 적는다", async () => {
    let 실패 = true;
    const 보낸: Array<Array<{ sql: string; args: unknown[] }>> = [];
    const send = async (s: Array<{ sql: string; args: unknown[] }>) => { if (실패) throw new Error("x"); 보낸.push(s); };
    await noteRouteReads([{ sql: "SELECT 1" }], READ_FLUSH_ROWS, new Date(), send, 스택);
    실패 = false;
    await noteRouteReads([{ sql: "SELECT 1" }], 1, new Date(), send, 스택);
    expect(JSON.parse(String(보낸[0][0].args[4])).rows).toBe(READ_FLUSH_ROWS + 1);
  });

  it("DB 층이 부르고, /status 최근 알림에서는 뺀다", () => {
    expect(readFileSync(join(process.cwd(), "lib", "db.ts"), "utf-8")).toContain("noteRouteReads(statements, read");
    expect(RECENT_HEALTH_ALERTS).toContain("job <> 'web_reads'");
  });
});
