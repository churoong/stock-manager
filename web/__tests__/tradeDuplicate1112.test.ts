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

describe("1분 안의 진짜 두 번째 체결을 확인하고 저장하면 들어간다 (docs/infra.md 25.2228)", () => {
  const T0 = "2026-10-10T01:00:00.000Z"; // 처음 저장 — 409 가 `seen_at` 으로 돌려준 시각
  const 뒤 = (초: number) => new Date(Date.parse(T0) + 초 * 1000).toISOString();

  it("앞 행이 1분 안이어도 confirm_after 를 실은 확인 저장은 넣고, 그 확인의 재전송은 막는다", async () => {
    const { CONFIRM_REPEAT_WINDOW_MS, insertLowerBound } = await import("@/lib/portfolio");
    expect(넣기(T0, insertLowerBound(Date.parse(T0), 10 * 60_000))).toBe(1);
    // 20초 뒤 "그래도 저장" — 앞 행(T0)은 이 확인이 알고 넘는 행이다
    expect(넣기(뒤(20), insertLowerBound(Date.parse(뒤(20)), CONFIRM_REPEAT_WINDOW_MS, T0))).toBe(1);
    // 그 확인 요청이 응답이 늦어 다시 왔다 — 같은 confirm_after
    expect(넣기(뒤(25), insertLowerBound(Date.parse(뒤(25)), CONFIRM_REPEAT_WINDOW_MS, T0))).toBe(0);
    expect((db.prepare("SELECT COUNT(*) AS n FROM trades").get() as { n: number }).n).toBe(2);
  });

  it("confirm_after 없는 확인(배포 사이 옛 화면)은 예전처럼 0행 — 경로는 저장된 것처럼 말하지 않는다", async () => {
    const { CONFIRM_REPEAT_WINDOW_MS, CONFIRM_UNSAVED_TRADE, insertLowerBound } = await import("@/lib/portfolio");
    expect(넣기(T0, insertLowerBound(Date.parse(T0), 10 * 60_000))).toBe(1);
    expect(넣기(뒤(20), insertLowerBound(Date.parse(뒤(20)), CONFIRM_REPEAT_WINDOW_MS))).toBe(0);
    expect((db.prepare("SELECT COUNT(*) AS n FROM trades").get() as { n: number }).n).toBe(1);
    expect(CONFIRM_UNSAVED_TRADE).toContain("저장하지 않았습니다");
    expect(CONFIRM_UNSAVED_TRADE).not.toContain("들어갔습니다");
    const 원문 = readFileSync(join(process.cwd(), "app", "api", "trades", "route.ts"), "utf-8");
    expect(원문).toContain("input.confirm_after ? \"확인하고 저장한 같은 매매가 방금(1분 안) 들어갔습니다");
    expect(원문).toContain(": CONFIRM_UNSAVED_TRADE]");
  });

  it("경계 — 지금 − 창과 confirm_after + 1ms 중 늦은 것, 지금을 넘지 않고, 못 읽는 값은 무시", async () => {
    const { insertLowerBound } = await import("@/lib/portfolio");
    const now = Date.parse(뒤(20));
    expect(insertLowerBound(now, 60_000, T0)).toBe("2026-10-10T01:00:00.001Z");
    expect(insertLowerBound(now, 60_000, "2026-10-10T00:50:00.000Z")).toBe("2026-10-10T00:59:20.000Z"); // 오래된 앞 행 — 창 그대로
    expect(insertLowerBound(now, 60_000, "2026-10-10T02:00:00.000Z")).toBe(뒤(20)); // 미래 값 — 지금으로 자름
    expect(insertLowerBound(now, 60_000, "깨짐")).toBe("2026-10-10T00:59:20.000Z");
    expect(insertLowerBound(now, 60_000)).toBe("2026-10-10T00:59:20.000Z");
  });

  it("created_at(toISOString) 의 글자 순서가 시간 순서다 — 경계 비교의 전제", () => {
    const 시각 = [0, 1, 999, 1000, 59_999, 86_400_000, 1_700_000_000_000, 1_799_999_999_999, 253_402_300_799_999].map((ms) => new Date(ms).toISOString());
    expect(시각.every((t) => t.length === 24 && t.endsWith("Z"))).toBe(true);
    expect([...시각].sort()).toEqual(시각);
  });

  it("경로가 409 에 seen_at 을 싣고 확인 저장의 경계에 confirm_after 를 쓴다", async () => {
    const 원문 = readFileSync(join(process.cwd(), "app", "api", "trades", "route.ts"), "utf-8");
    expect(원문).toContain("seen_at: 같은것.created_at");
    expect(원문).toContain("insertLowerBound(Date.now(), CONFIRM_REPEAT_WINDOW_MS, input.confirm_after)");
    const { tradeInputSchema } = await import("@/lib/portfolio");
    const 기본 = { stock_id: 1, side: "buy", trade_date: "2026-10-08", price: 70000, quantity: 10, confirm_duplicate: true };
    expect(tradeInputSchema.safeParse({ ...기본, confirm_after: T0 }).success).toBe(true);
    expect(tradeInputSchema.safeParse({ ...기본, confirm_after: "어제" }).success).toBe(false);
    expect(tradeInputSchema.safeParse(기본).success).toBe(true);
  });
});

describe("seen_at 은 가장 새 같은 행이고, 끼어든 행의 409 에도 실린다 (25.2229, 25.2228 교차검증 [권고])", () => {
  it("RECENT_SAME_TRADE 는 가장 새 행을 돌려주고 셋째 체결의 확인 저장이 들어간다", async () => {
    const { CONFIRM_REPEAT_WINDOW_MS, RECENT_SAME_TRADE, insertLowerBound } = await import("@/lib/portfolio");
    const T0 = "2026-10-10T01:00:00.000Z";
    const T10 = "2026-10-10T01:00:10.000Z";
    expect(넣기(T0, insertLowerBound(Date.parse(T0), 10 * 60_000))).toBe(1);
    expect(넣기(T10, insertLowerBound(Date.parse(T10), CONFIRM_REPEAT_WINDOW_MS, T0))).toBe(1);
    const 본 = db.prepare(RECENT_SAME_TRADE).get(1, "buy", "2026-10-08", 70000, 10, "2026-10-10T00:00:00.000Z") as {
      created_at: string;
    };
    expect(본.created_at).toBe(T10); // ASC 면 T0 — 셋째 확인이 둘째 행에 막혀 거짓 '들어갔습니다' 로 돌아간다
    const T20 = "2026-10-10T01:00:20.000Z";
    expect(넣기(T20, insertLowerBound(Date.parse(T20), CONFIRM_REPEAT_WINDOW_MS, 본.created_at))).toBe(1);
  });

  it("배당 질의도 가장 새 행부터", async () => {
    const { RECENT_SAME_DIVIDEND } = await import("@/lib/portfolio");
    expect(RECENT_SAME_DIVIDEND).toMatch(/ORDER BY created_at DESC LIMIT 1/);
  });

  it("확인과 쓰기 사이에 끼어든 행의 409 도 seen_at 을 싣는다 — 매매·배당 둘 다", () => {
    for (const 경로 of ["trades", "dividends"]) {
      const 원문 = readFileSync(join(process.cwd(), "app", "api", 경로, "route.ts"), "utf-8");
      expect(원문, 경로).toContain("seen_at: 끼어든.created_at");
    }
  });
});
