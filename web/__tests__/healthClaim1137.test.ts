import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { HEALTH_CLAIM_PREFIX, HEALTH_CLAIM_STALE_MS, PENDING_HEALTH_ALERTS } from "@/lib/health";

/** 무응답 경보를 잡고 발송 중 죽으면 영영 안 가던 것 (docs/infra.md 25.1137, cron 감사 재현) */
describe("잡고 죽은 무응답 경보", () => {
  it("10분이 지나면 다시 보낼 것에 든다, 보낸 것은 들지 않는다", () => {
    const db = new DatabaseSync(":memory:");
    const dir = join(process.cwd(), "..", "migrations");
    for (const f of readdirSync(dir).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(dir, f), "utf-8"));
    const 넣기 = (id: number, sent: string | null) =>
      db.prepare(`INSERT INTO health_alerts (id, job, market, local_date, kind, message, data, created_at, sent_at)
        VALUES (?, ?, 'KR', '2026-10-12', 'no_run', 'm', '{}', '2026-10-12T01:00:00Z', ?)`).run(id, `j${id}`, sent);
    넣기(1, null); // 안 보냄
    넣기(2, `${HEALTH_CLAIM_PREFIX}2026-10-12T01:10:00.000Z`); // 잡고 죽음 — 20분 전
    넣기(3, `${HEALTH_CLAIM_PREFIX}2026-10-12T01:25:00.000Z`); // 지금 다른 호출이 잡고 보내는 중 — 5분 전
    넣기(4, "2026-10-12T01:10:00.000Z"); // 보냄
    const 지금 = new Date("2026-10-12T01:30:00Z");
    const 묵은 = HEALTH_CLAIM_PREFIX + new Date(지금.getTime() - HEALTH_CLAIM_STALE_MS).toISOString();
    const ids = (db.prepare(PENDING_HEALTH_ALERTS).all(묵은) as Array<{ id: number }>).map((r) => r.id).sort();
    expect(ids).toEqual([1, 2]);
  });

  it("경로는 claim 으로 잡고 보낸 뒤 시각을 적는다", () => {
    const src = readFileSync(join(process.cwd(), "app", "api", "cron", "health", "route.ts"), "utf-8");
    expect(src).toContain("HEALTH_CLAIM_PREFIX + now.toISOString(), row.id, 묵은잡음");
    expect(src).toContain("execute(PENDING_HEALTH_ALERTS, [묵은잡음])");
    expect(src).toMatch(/export const maxDuration = \d+/);
  });
});

describe("장중 감시 목록의 깨진 사유 행", () => {
  it("빈 배열로 읽고 던지지 않는다", async () => {
    const { parseReasons } = await import("@/lib/intraday");
    expect(parseReasons('["holding","signal"]')).toEqual(["holding", "signal"]);
    expect(parseReasons("holding")).toEqual([]);
    expect(parseReasons(null)).toEqual([]);
    expect(parseReasons('{"a":1}')).toEqual([]);
    const src = readFileSync(join(process.cwd(), "app", "api", "cron", "intraday", "route.ts"), "utf-8");
    expect(src).toContain("reasons: parseReasons(t.reasons)");
    expect(src).not.toContain("JSON.parse(String(t.reasons))");
  });
});
