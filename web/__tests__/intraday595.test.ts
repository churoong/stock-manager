import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeAll, describe, expect, it } from "vitest";
import { DISCLOSURE_ALERTS_FOR } from "@/lib/intraday";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

beforeAll(() => {
  db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIGRATIONS, f), "utf-8"));
});

const 계획 = (sql: string, ...args: Array<string | number>) =>
  (db.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...args) as Array<{ detail: string }>).map((r) => r.detail);

describe("장중 경로가 alerts 를 통째로 훑지 않는다 (docs/infra.md 25.595)", () => {
  it("공시 알림 조회는 유니크 색인을 탄다", () => {
    const p = 계획(DISCLOSURE_ALERTS_FOR, "[1,2]", "2026-09-28");
    expect(p.some((d) => /SCAN a\b|SCAN alerts/.test(d)), p.join("\n")).toBe(false);
  });

  it("발송 대기 조회는 이미 보낸 행을 범위에 넣지 않는다", () => {
    const 글 = readFileSync("app/api/cron/intraday/route.ts", "utf8");
    expect(글).toContain("OR (sent_at >= 'claim:' AND sent_at < ?)");
    expect(글).not.toContain("sent_at LIKE 'claim:%' AND sent_at < ?");
  });

  it("외부 호출에 제한 시간이 있고, 시세 알림은 DART 보다 먼저 저장한다", () => {
    const 글 = readFileSync("app/api/cron/intraday/route.ts", "utf8");
    expect(글).toContain("signal: AbortSignal.timeout(QUOTE_TIMEOUT_MS)");
    expect(글).toContain("signal: AbortSignal.timeout(DART_TIMEOUT_MS)");
    expect(글.indexOf("const inserted = await 넣기(hits);")).toBeLessThan(글.indexOf("const dart = await disclosures("));
    expect(readFileSync("lib/telegram.ts", "utf8")).toContain("signal: AbortSignal.timeout(10_000)");
  });
});

describe("관심 종목만 있는 종목의 기업행위 가드 (25.595)", () => {
  it("상수가 배치 adjust.py·monitor_targets.py 와 같다", async () => {
    const m = await import("@/lib/intraday");
    const adj = readFileSync(join(process.cwd(), "..", "batch", "services", "adjust.py"), "utf-8");
    const mt = readFileSync(join(process.cwd(), "..", "batch", "jobs", "monitor_targets.py"), "utf-8");
    expect(adj).toMatch(new RegExp(`^ACTION_TOLERANCE = ${m.ADJUST_ACTION_TOLERANCE}$`, "m"));
    expect(adj).toMatch(new RegExp(`^MIN_FACTOR = ${m.ADJUST_MIN_FACTOR}$`, "m"));
    expect(adj).toMatch(/^MAX_FACTOR = 200\.0$/m);
    expect(mt).toMatch(new RegExp(`^ACTION_GUARD_ROWS = ${m.ACTION_GUARD_ROWS}$`, "m"));
  });

  it("1:50 분할(종가 1/50, 등락률 0%)을 기업행위로 본다, 평범한 날은 아니다", async () => {
    const { recentActionKr, recentActionUs } = await import("@/lib/intraday");
    expect(recentActionKr([{ close: 2000, change_pct: 0 }, { close: 100000, change_pct: 1 }])).toBe(true);
    expect(recentActionKr([{ close: 101000, change_pct: 1 }, { close: 100000, change_pct: 0 }])).toBe(false);
    expect(recentActionUs("2026-09-20T00:00:00+00:00", new Date("2026-09-28T00:00:00Z"))).toBe(true);
    expect(recentActionUs("2026-07-01T00:00:00+00:00", new Date("2026-09-28T00:00:00Z"))).toBe(false);
  });
});

describe("기업행위 첫날 의심 (docs/infra.md 25.597)", () => {
  it("국내 1:5 분할 재개일은 제한폭 밖이라 의심한다, 평범한 −10% 는 아니다", async () => {
    const { actionSuspect } = await import("@/lib/intraday");
    expect(actionSuspect("KR", 19_800, 97_500, 97_500)).toBe(true);
    expect(actionSuspect("KR", 87_750, 97_500, 97_500)).toBe(false);
    expect(actionSuspect("KR", 19_800, null, 97_500), "모르면 의심하지 않는다").toBe(false);
  });

  it("미국은 넓게 본다 — 3:2 분할 첫날은 놓치는 대신 DB 가 묵은 날 손절을 지우지 않는다 (25.600)", async () => {
    const { actionSuspect } = await import("@/lib/intraday");
    expect(actionSuspect("US", 50, 150, 150)).toBe(true); // 1:3, 야후 미조정
    expect(actionSuspect("US", 50, 150, 50.5)).toBe(true); // 1:3, 야후가 조정 — 야후/DB 가 범위 밖
    expect(actionSuspect("US", 95, 150, 100)).toBe(false); // 3:2 는 놓친다(알고 둔다)
    expect(actionSuspect("US", 100, 150, 150)).toBe(false); // −33% 진짜 급락은 의심하지 않는다
    expect(actionSuspect("US", 104, 100, 108)).toBe(false); // DB 가 하루 묵고 어제 +8% — 예전 5% 규칙은 손절을 지웠다
  });

  it("국내: DB 전일 종가가 묵어 이틀 누적이 제한폭 밖이어도 야후 전일 종가 기준으로 범위 안이면 의심하지 않는다 (25.600)", async () => {
    const { actionSuspect } = await import("@/lib/intraday");
    // 어제 −25%(100,000 → 75,000), 오늘 −10%(67,500). DB 는 그저께 100,000 에 머묾
    expect(actionSuspect("KR", 67_500, 100_000, 75_000)).toBe(false);
    // 야후가 없으면 예전처럼 의심한다(모르면 가격 알림을 덜 내는 쪽)
    expect(actionSuspect("KR", 67_500, 100_000, null)).toBe(true);
  });

  it("전일 종가의 1.5배를 넘는 관심 목표 매수가는 쓰지 않는다 — 1:2 분할은 분할 전 75% 위 목표만 거른다 (25.600·25.602·25.603)", async () => {
    const { watchPriceUsable } = await import("@/lib/intraday");
    expect(watchPriceUsable(90_000, 2_000)).toBeNull();
    expect(watchPriceUsable(90_000, 50_000), "1:2 분할 — 분할 전 100,000 에 적은 90,000").toBeNull();
    expect(watchPriceUsable(70_000, 50_000), "분할 전 70% 에 적은 목표는 못 거른다(알고 둔다)").toBe(70_000);
    expect(watchPriceUsable(1_900, 2_000)).toBe(1_900);
    expect(watchPriceUsable(90_000, null)).toBe(90_000);
  });

  it("의심이면 손절·목표·관심 목표가·거래량은 빼고, 미국만 급등락은 남긴다", async () => {
    const { dropOnActionSuspect } = await import("@/lib/intraday");
    expect(dropOnActionSuspect("KR").has("stop")).toBe(true);
    expect(dropOnActionSuspect("KR").has("spike_down")).toBe(true);
    expect(dropOnActionSuspect("US").has("spike_down")).toBe(false);
    expect(dropOnActionSuspect("US").has("watch_price")).toBe(true);
    const 글 = readFileSync("app/api/cron/intraday/route.ts", "utf8");
    expect(글).toContain("if (actionSuspect(market, quote.price, target.prev_close, quote.previous_close)) {");
  });
});

describe("교차검증 반영 (docs/infra.md 25.598)", () => {
  it("DART 확인에 전체 마감 시간이 있고, 관심 가드는 한 요청으로 묶고 목표 매수가도 막는다", () => {
    const 글 = readFileSync("app/api/cron/intraday/route.ts", "utf8");
    expect(글).toContain("if (Date.now() - now.getTime() > DART_BUDGET_MS) {");
    expect(글).toContain("const 결과 = await batch(관심만.map((w) => ({");
    expect(글).toContain("existing.watchBuy = 가드된.has(w.stock_id) ? null : w.target_buy_price;");
    expect(글).toContain('...targets.filter((t) => t.reasons.includes("action_guard")).map((t) => t.stock_id),');
  });

  it("국내 감성 단추는 batch_runs 의 sentiment(KR) 기록으로 잠긴다", async () => {
    const { findJob } = await import("@/lib/dispatch");
    expect(findJob("sentiment_kr")?.run).toEqual({ name: "sentiment", market: "KR" });
    // 상태 화면의 수동 실행 칸은 25.879 에서 뺐다(사용자 지시) — 작업 표의 연결만 남는다
  });
});
