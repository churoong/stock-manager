/** 무거운 읽기 기록 (docs/health.md 7.2, docs/infra.md 25.956) — 월 카운터는 합만 알고 출처를 몰랐다. */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { HEAVY_READ_JOB, HEAVY_READ_ROWS, heavyReadRecord, routeFromStack, sqlShape } from "@/lib/webUsage";

const 지금 = new Date("2026-10-05T05:00:00Z");
const 스택 = `Error
    at tursoBatch (/var/task/.next/server/chunks/lib/db.ts:200:5)
    at async GET (/var/task/.next/server/app/api/recommend/route.ts:150:20)`;

describe("heavyReadRecord", () => {
  it("문턱을 넘으면 질의 모양·행 수·경로를 남기고, 값(인자)은 남기지 않는다", () => {
    const rec = heavyReadRecord([{ sql: "SELECT  p.close\n FROM prices p WHERE p.stock_id = ?" }], 7_000_000, 지금, 스택);
    expect(rec).not.toBeNull();
    expect(rec!.args[0]).toBe(HEAVY_READ_JOB);
    expect(rec!.args[1]).toBe("2026-10-05");
    expect(rec!.args[2]).toBe("SELECT p.close FROM prices p WHERE p.stock_id = ?");
    expect(String(rec!.args[3])).toContain("7,000,000행");
    const data = JSON.parse(String(rec!.args[4]));
    expect(data).toMatchObject({ rows: 7_000_000, count: 1, route: "app/api/recommend/route" });
    expect(rec!.args[6]).toBe(rec!.args[5]); // sent_at 을 채워 텔레그램으로 나가지 않는다
  });

  it("문턱 아래·빈 묶음은 null", () => {
    expect(heavyReadRecord([{ sql: "SELECT 1" }], HEAVY_READ_ROWS - 1, 지금, 스택)).toBeNull();
    expect(heavyReadRecord([], 10_000_000, 지금, 스택)).toBeNull();
  });

  it("질의 모양과 경로", () => {
    expect(sqlShape("  SELECT   a,\n  b FROM t  ")).toBe("SELECT a, b FROM t");
    expect(routeFromStack(스택)).toBe("app/api/recommend/route"); // DB 층(lib/db)이 아니라 부른 경로
    expect(routeFromStack("Error\n at a (/x/lib/db.ts:1:1)\n at b (/x/lib/portfolio.ts:2:2)")).toBe("lib/portfolio");
    expect(routeFromStack("Error\n    at x (/var/task/.next/server/app/api/stocks/[id]/route.ts:1:1)")).toBe("app/api/stocks/[id]/route");
    expect(routeFromStack(undefined)).toBe("?");
  });

  it("DB 층이 읽기마다 부른다", () => {
    const src = readFileSync(join(process.cwd(), "lib", "db.ts"), "utf-8");
    expect(src).toContain("noteHeavyRead(statements, read");
  });
});
