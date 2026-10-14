/** 모아보기가 결론·시장마다 자르고 실제 개수를 함께 낸다 (docs/infra.md 25.1339, 분석 화면 감사 #4) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { HUB_LIMIT, HUB_SQL, groupHub, type HubRow } from "@/lib/analysis";

const ROOT = join(process.cwd(), "..");

function memDb(): DatabaseSync {
  const db = new DatabaseSync(":memory:");
  for (const f of readdirSync(join(ROOT, "migrations")).filter((x) => x.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(ROOT, "migrations", f), "utf-8"));
  }
  return db;
}

describe("모아보기 자르기", () => {
  it("국내 매수 검토가 많아도 미국 매수 검토가 빠지지 않고, 머리 숫자는 실제 개수", () => {
    const db = memDb();
    let id = 0;
    const 넣기 = (country: string, verdict: string, n: number) => {
      for (let i = 0; i < n; i++) {
        id += 1;
        db.prepare(`INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at) VALUES (?, ?, 'X', ?, 'KRW', 't', 't')`)
          .run(id, `${country}${String(id).padStart(4, "0")}`, country);
        db.prepare(`INSERT INTO stock_verdicts VALUES (?, ?, ?, ?, '{}', '[]', NULL, NULL, 't')`).run(id, country, verdict, `${verdict} — x`);
      }
    };
    넣기("KR", "consider_buy", 35);
    넣기("US", "consider_buy", 4);
    넣기("KR", "check_holding", 100); // 예전: 120줄 상한에서 보유 유지 묶음이 사라졌다
    넣기("KR", "hold", 2);
    const rows = db.prepare(HUB_SQL).all() as unknown as HubRow[];
    const g = Object.fromEntries(groupHub(rows).map((x) => [x.key, x]));
    expect(g.consider_buy.total).toBe(39);
    expect(g.consider_buy.rows.filter((r) => r.market === "US")).toHaveLength(4); // 예전: 0
    expect(g.consider_buy.rows.filter((r) => r.market === "KR")).toHaveLength(HUB_LIMIT);
    expect(g.hold.rows).toHaveLength(2);
    expect(g.check_holding.total).toBe(100);
  });
});
