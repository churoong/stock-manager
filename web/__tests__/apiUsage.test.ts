/**
 * 웹이 부른 바깥 API 를 한도 카운터에 센다 (docs/infra.md 25.105).
 *
 * 2026-09-22 까지 `api_usage` 에 쓰는 곳은 배치뿐이었다. 장중 경로가 보유 종목마다 DART
 * 공시를 직접 부르는데 그 호출은 **어디에도 세지지 않았다.** 그래서 `/status` 의 DART
 * 게이지가 실제보다 적게 보였고, 배치는 이미 웹이 쓴 여유를 제 것으로 알고 계속 불렀다.
 *
 * 질의는 **실제 마이그레이션 스키마**에 돌린다. 판정(80/100)은 순수 함수라 손으로 본다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usageRatio, usageTone } from "@/lib/limits";

const ROOT = join(process.cwd(), "..");
const NOW = new Date("2026-09-22T05:00:00Z");

/** 실제 스키마 위의 가짜 DB. `@/lib/db` 의 execute 자리에 끼운다 */
function sqliteBackend() {
  const db = new DatabaseSync(":memory:");
  for (const f of readdirSync(join(ROOT, "migrations")).filter((n) => n.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(ROOT, "migrations", f), "utf-8"));
  }
  const seen: string[] = [];
  const execute = async (sql: string, args: unknown[] = []) => {
    seen.push(sql);
    const stmt = db.prepare(sql);
    if (/^\s*select/i.test(sql)) {
      const rows = stmt.all(...(args as never[])) as Array<Record<string, unknown>>;
      const columns = rows.length ? Object.keys(rows[0]) : [];
      return { columns, rows: rows.map((r) => columns.map((c) => r[c])), affectedRows: 0 };
    }
    const info = stmt.run(...(args as never[]));
    return { columns: [], rows: [], affectedRows: Number(info.changes) };
  };
  return { db, execute, seen };
}

describe("한도 판정 (limits.ts 가 단일 정의처다)", () => {
  it("80% 경고 · 100% 차단 · 한도를 모르면 unknown", () => {
    expect(usageTone(usageRatio(16_000, 20_000), 80)).toBe("warn");
    expect(usageTone(usageRatio(15_999, 20_000), 80)).toBe("ok");
    expect(usageTone(usageRatio(20_000, 20_000), 80)).toBe("blocked");
    expect(usageTone(usageRatio(7, null), 80)).toBe("unknown");
  });

  it("화면이 쓰던 이름 그대로 남아 있다 — 옮기면서 끊기지 않았다", async () => {
    const health = await import("@/lib/health");
    expect(health.usageTone(usageRatio(1, 1), 80)).toBe("blocked");
    expect(health.usageRatio(5, 0)).toBeNull();
  });
});

describe("세기", () => {
  let backend: ReturnType<typeof sqliteBackend>;

  beforeEach(() => {
    vi.resetModules();
    backend = sqliteBackend();
    vi.doMock("@/lib/db", async (orig) => ({
      ...(await (orig as () => Promise<Record<string, unknown>>)()),
      execute: backend.execute,
    }));
  });
  afterEach(() => {
    vi.doUnmock("@/lib/db");
    backend.db.close();
  });

  it("기록이 없으면 0 에서 시작한다 (없는 것을 '모름' 으로 두지 않는다)", async () => {
    const { readUsage, DART_API_NAME, DART_DAILY_LIMIT } = await import("@/lib/apiUsage");
    const usage = await readUsage(DART_API_NAME, NOW, DART_DAILY_LIMIT);
    expect(usage.call_count).toBe(0);
    expect(usage.limit_value).toBe(DART_DAILY_LIMIT);
    expect(usage.warn_at_pct).toBe(80);
  });

  it("실제로 나간 횟수만큼 더하고, 한 번 부를 때 **한 줄만** 쓴다", async () => {
    const { addUsage, readUsage, DART_API_NAME, DART_DAILY_LIMIT } = await import("@/lib/apiUsage");
    await addUsage(DART_API_NAME, 7, NOW, DART_DAILY_LIMIT);
    await addUsage(DART_API_NAME, 5, NOW, DART_DAILY_LIMIT);
    expect((await readUsage(DART_API_NAME, NOW)).call_count).toBe(12);
    // 호출마다 한 줄씩 쓰면 5분 크론이 하루 수백 번 쓴다. 더하기 문장은 두 번뿐이어야 한다
    expect(backend.seen.filter((s) => /^INSERT INTO api_usage/.test(s))).toHaveLength(2);
  });

  it("배치와 같은 행에 쌓인다 — 세는 자리가 갈리면 한도 판정이 틀린다 (25.68)", async () => {
    const { addUsage, readUsage, DART_API_NAME, DART_DAILY_LIMIT } = await import("@/lib/apiUsage");
    backend.db.exec(`INSERT INTO api_usage (api_name, window_type, window_start, call_count, limit_value, warn_at_pct, state, updated_at)
      VALUES ('dart_opendart', 'day', '2026-09-22', 15_990, 20000, 80, 'ok', 't')`);
    await addUsage(DART_API_NAME, 20, NOW, DART_DAILY_LIMIT);
    const usage = await readUsage(DART_API_NAME, NOW);
    expect(usage.call_count).toBe(16_010);
    const state = backend.db.prepare("SELECT state FROM api_usage WHERE api_name = 'dart_opendart'").get() as { state: string };
    expect(state.state).toBe("warn"); // 80% 를 우리 쪽 호출이 넘겼다
  });

  it("한도를 넘기면 blocked 로 적힌다", async () => {
    const { addUsage, readUsage, isBlocked, DART_API_NAME, DART_DAILY_LIMIT } = await import("@/lib/apiUsage");
    await addUsage(DART_API_NAME, DART_DAILY_LIMIT, NOW, DART_DAILY_LIMIT);
    expect(isBlocked(await readUsage(DART_API_NAME, NOW))).toBe(true);
  });

  it("DART 가 스스로 '제한 초과' 라 하면 우리 셈이 모자라도 blocked 다", async () => {
    // 한도가 계정마다 다를 수 있다(docs/data-sources.md). **바깥이 막혔다면 막힌 것이다**
    const { addUsage, readUsage, isBlocked, DART_API_NAME, DART_DAILY_LIMIT } = await import("@/lib/apiUsage");
    await addUsage(DART_API_NAME, 3, NOW, DART_DAILY_LIMIT, { blocked: true });
    const row = backend.db.prepare("SELECT call_count, state FROM api_usage").get() as { call_count: number; state: string };
    expect(row.call_count).toBe(3);
    expect(row.state).toBe("blocked");
    expect(isBlocked(await readUsage(DART_API_NAME, NOW))).toBe(true);
  });

  it("한 번도 안 불렀으면 아무것도 쓰지 않는다", async () => {
    const { addUsage, DART_API_NAME, DART_DAILY_LIMIT } = await import("@/lib/apiUsage");
    await addUsage(DART_API_NAME, 0, NOW, DART_DAILY_LIMIT);
    expect(backend.seen.filter((s) => /api_usage/.test(s))).toHaveLength(0);
  });

  it("한도를 모르면 막지 않는다 — 모르는 것을 나쁘다고 단정하지 않는다 (25.0)", async () => {
    const { isBlocked } = await import("@/lib/apiUsage");
    expect(isBlocked({ call_count: 1_000_000, limit_value: null, warn_at_pct: 80, state: "unknown" })).toBe(false);
  });

  it("적어 둔 blocked 를 **읽을 때 건다** — 적어 놓고 안 걸면 없는 것과 같다", async () => {
    // 계정마다 한도가 다를 수 있어 우리 셈은 늘 한도에 못 미친다. 셈으로만 보면
    // 바깥이 "제한 초과" 라고 답해서 적어 둔 사실이 다음 호출에서 그대로 무시된다
    const { isBlocked } = await import("@/lib/apiUsage");
    const 적게썼다 = { call_count: 3, limit_value: 20_000, warn_at_pct: 80 };
    expect(isBlocked({ ...적게썼다, state: "blocked" })).toBe(true);
    expect(isBlocked({ ...적게썼다, state: "ok" })).toBe(false);
  });
});
