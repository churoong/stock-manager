/** 조용시간에 "보내지 않음" 으로 둔 알림을 조용시간 밖에서 되살린다 (docs/infra.md 25.797, 알림 감사 #1) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { ALERT_UPSERT } from "@/lib/alertWrite";

function 새_db(): DatabaseSync {
  const d = new DatabaseSync(":memory:");
  for (const f of readdirSync(join(process.cwd(), "..", "migrations")).filter((x) => x.endsWith(".sql")).sort()) {
    d.exec(readFileSync(join(process.cwd(), "..", "migrations", f), "utf-8"));
  }
  d.exec(`INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
    VALUES (1, 'AAPL', 'NASDAQ', 'US', NULL, 'USD', 'active', 't', 't')`);
  return d;
}
const 넣기 = (d: DatabaseSync, sent: string | null, msg: string) =>
  d.prepare(ALERT_UPSERT).run(1, "US", "2026-09-30", "stop", msg, "{}", "2026-09-30T13:35:00Z", sent).changes;

describe("알림 저장", () => {
  it("조용시간에 '보내지 않음' 으로 둔 것은 조용시간 밖의 같은 판정이 되살린다", () => {
    const d = 새_db();
    expect(넣기(d, "quiet-skipped", "손절 22:35")).toBe(1);
    // 01:05 호출 — 조용시간 밖이라 sent_at NULL 로 온다 → 되살아나 이번 호출이 보낸다
    expect(넣기(d, null, "손절 01:05")).toBe(1);
    const 행 = d.prepare("SELECT sent_at, message FROM alerts").get() as { sent_at: string | null; message: string };
    expect(행).toEqual({ sent_at: null, message: "손절 01:05" });
  });

  it("조용시간 안의 되풀이·이미 보낸 알림은 그대로 — 하루 한 번", () => {
    const d = 새_db();
    넣기(d, "quiet-skipped", "처음");
    expect(넣기(d, "quiet-skipped", "또")).toBe(0);
    const e = 새_db();
    넣기(e, null, "보낼 것");
    e.exec("UPDATE alerts SET sent_at = '2026-09-30T13:36:00Z'");
    expect(넣기(e, null, "또")).toBe(0);
    expect((e.prepare("SELECT message FROM alerts").get() as { message: string }).message).toBe("보낼 것");
  });

  it("장중 경로가 이 문장을 쓴다", () => {
    expect(readFileSync("app/api/cron/intraday/route.ts", "utf-8")).toContain("sql: ALERT_UPSERT,");
  });
});

describe("거래량 알림 문구", () => {
  it("문턱(3배)을 넘은 3.04배를 '3.0배' 로 적지 않는다 (25.797, 알림 감사 #3)", async () => {
    const { evaluate } = await import("@/lib/intraday");
    const 대상 = {
      stock_id: 1, ticker: "005930", market: "KOSPI", currency: "KRW", name: "삼성전자", reasons: ["보유"],
      buy_zone_low: null, buy_zone_high: null, target_price: null, stop_price: null, prev_close: null, avg_volume_20d: 1_000_000,
    };
    const 시세 = { symbol: "005930.KS", price: 70_000, time: "2026-09-30T02:00:00.000Z", previous_close: null, day_high: null, day_low: null, volume: 3_040_000 };
    const hits = evaluate(대상 as never, 시세 as never, { spike_pct: 10, volume_multiple: 3 } as never) as Array<{ trigger: string; message: string }>;
    expect(hits.find((h) => h.trigger === "volume")?.message).toContain("3.04배");
    const 넉넉 = evaluate(대상 as never, { ...시세, volume: 4_500_000 } as never, { spike_pct: 10, volume_multiple: 3 } as never) as Array<{ trigger: string; message: string }>;
    expect(넉넉.find((h) => h.trigger === "volume")?.message).toContain("4.5배");
  });
});
