import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeEach, describe, expect, it } from "vitest";
import { TRADE_INSERT_UNLESS_RECENT } from "@/lib/portfolio";

/**
 * **중복 확인과 쓰기를 한 문장으로** (docs/infra.md 25.1112, 웹 매매 감사 재현).
 *
 * 확인(조회)과 INSERT 사이에 DB 조회가 너덧 번 있어, 같은 본문을 동시에 두 번 보내면 둘 다 200·두 행이었다.
 * SQLite 는 쓰기를 한 줄로 세우므로 한 문장 안의 NOT EXISTS 는 앞 요청의 행을 본다.
 */
const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  db.exec(`INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
    VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')`);
});

function 넣기(now: string, 이후: string, qty = 10): number {
  const 값 = [1, "buy", "2026-10-08", 70000, qty, "KRW", 1, "none", null, null, "long", null, null, null, null, null, null, now, now];
  const r = db.prepare(TRADE_INSERT_UNLESS_RECENT).run(...(값 as never[]), 1, "buy", "2026-10-08", 70000, qty, 이후);
  return Number(r.changes);
}

describe("같은 매매가 방금 있으면 한 문장 안에서 넣지 않는다", () => {
  it("두 번째 같은 요청은 0행", () => {
    expect(넣기("2026-10-10T01:00:00.000Z", "2026-10-10T00:50:00.000Z")).toBe(1);
    expect(넣기("2026-10-10T01:00:01.000Z", "2026-10-10T00:50:01.000Z")).toBe(0);
    expect((db.prepare("SELECT COUNT(*) AS n FROM trades").get() as { n: number }).n).toBe(1);
  });

  it("수량이 다르거나 창(10분) 밖이면 넣는다", () => {
    expect(넣기("2026-10-10T01:00:00.000Z", "2026-10-10T00:50:00.000Z")).toBe(1);
    expect(넣기("2026-10-10T01:00:01.000Z", "2026-10-10T00:50:01.000Z", 5)).toBe(1);
    expect(넣기("2026-10-10T01:20:00.000Z", "2026-10-10T01:10:00.000Z")).toBe(1);
  });
});

describe("경로가 확인 없는 저장에 한 문장 쓰기를 쓴다", () => {
  it("confirm_duplicate 가 없으면 TRADE_INSERT_UNLESS_RECENT, 0행이면 409", () => {
    const 원문 = readFileSync(join(process.cwd(), "app", "api", "trades", "route.ts"), "utf-8");
    expect(원문).toContain("execute(TRADE_INSERT_UNLESS_RECENT");
    expect(원문).toMatch(/affectedRows === 0[\s\S]{0,400}status: 409/);
  });
});

describe("주말 체결일은 말한다 (docs/infra.md 25.1114)", () => {
  it("토·일만", async () => {
    const { weekendNote } = await import("@/lib/portfolio");
    expect(weekendNote("2026-10-03")).toContain("토요일");
    expect(weekendNote("2026-10-04")).toContain("일요일");
    expect(weekendNote("2026-10-08")).toBeNull();
    expect(weekendNote("깨짐")).toBeNull();
    const 원문 = readFileSync(join(process.cwd(), "app", "api", "trades", "route.ts"), "utf-8");
    expect(원문).toContain("weekendNote(input.trade_date)");
  });
});

describe("확인하고 저장한 요청도 1분 안의 재전송은 한 행만 (docs/infra.md 25.1356)", () => {
  it("앞 행이 1분보다 오래면 넣고, 그 확인 저장이 다시 오면 0행", async () => {
    const { CONFIRM_REPEAT_WINDOW_MS } = await import("@/lib/portfolio");
    const 창 = (now: string) => new Date(Date.parse(now) - CONFIRM_REPEAT_WINDOW_MS).toISOString();
    expect(넣기("2026-10-10T01:00:00.000Z", 창("2026-10-10T01:00:00.000Z"))).toBe(1); // 처음 저장
    // 2분 뒤 "그래도 저장" — 앞 행을 알고 넣는 길이다
    expect(넣기("2026-10-10T01:02:00.000Z", 창("2026-10-10T01:02:00.000Z"))).toBe(1);
    // 그 확인 요청이 응답이 늦어 다시 왔다
    expect(넣기("2026-10-10T01:02:05.000Z", 창("2026-10-10T01:02:05.000Z"))).toBe(0);
    expect((db.prepare("SELECT COUNT(*) AS n FROM trades").get() as { n: number }).n).toBe(2);
  });

  it("매매·배당 경로 모두 장치 없는 INSERT 를 쓰지 않는다", () => {
    const 매매 = readFileSync(join(process.cwd(), "app", "api", "trades", "route.ts"), "utf-8");
    const 배당 = readFileSync(join(process.cwd(), "app", "api", "dividends", "route.ts"), "utf-8");
    expect(매매).not.toMatch(/execute\(TRADE_INSERT,/);
    expect(배당).not.toMatch(/execute\(DIVIDEND_INSERT,/);
    expect(매매).toContain("CONFIRM_REPEAT_WINDOW_MS");
    expect(배당).toContain("CONFIRM_REPEAT_WINDOW_MS");
  });
});
