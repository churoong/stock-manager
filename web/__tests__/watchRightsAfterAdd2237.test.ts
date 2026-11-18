import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { WATCH_RIGHTS_AFTER_ADD_SQL, rightsAfterAddGuard } from "@/lib/intraday";

/**
 * 관심 목표가 — 넣은 날 뒤 유상증자 권리락이 있었으면 20거래일 가드가 풀린 뒤에도 막는다 (docs/infra.md 25.2237).
 * 예전엔 21번째 거래일부터 권리락 전 단위 목표가 50,000 이 권리락 뒤 49,000 과 견줘져 거짓 "도달" 이 날마다 나갔다.
 */
const MIGRATIONS = join(process.cwd(), "..", "migrations");

function db(): DatabaseSync {
  const d = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((x) => x.endsWith(".sql")).sort()) d.exec(readFileSync(join(MIGRATIONS, f), "utf-8"));
  d.exec(`INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
    VALUES (1, '000001', 'KOSPI', 'KR', 'x', 'KRW', 'active', 't', 't')`);
  return d;
}

function 셈(d: DatabaseSync): number {
  const r = d.prepare(`SELECT ${WATCH_RIGHTS_AFTER_ADD_SQL} AS n FROM watchlist w CROSS JOIN stocks s ON s.id = w.stock_id`).get() as { n: number };
  return Number(r.n);
}

describe("넣은 날 뒤 권리락 수", () => {
  it("넣은 뒤 지난 권리락이면 1, 넣기 전 권리락·다른 종류는 0", () => {
    const d = db();
    const cols = (d.prepare("PRAGMA table_info(watchlist)").all() as { name: string }[]).map((c) => c.name);
    expect(cols).toContain("added_at");
    d.exec("INSERT INTO watchlist (stock_id, target_buy_price, alert_enabled, added_at) VALUES (1, 50000, 1, '2026-09-01T00:00:00Z')");
    d.exec("INSERT INTO kr_corp_events (code, kind, record_date, source, fetched_at) VALUES ('000001', 'rights', '2026-08-20', 't', 't')");
    d.exec("INSERT INTO kr_corp_events (code, kind, record_date, source, fetched_at) VALUES ('000001', 'dividend', '2026-10-01', 't', 't')");
    expect(셈(d)).toBe(0);
    d.exec("INSERT INTO kr_corp_events (code, kind, record_date, source, fetched_at) VALUES ('000001', 'rights', '2026-10-05', 't', 't')");
    expect(셈(d)).toBe(1);
  });

  it("먼 미래 기준일은 아직 막지 않는다", () => {
    const d = db();
    d.exec("INSERT INTO watchlist (stock_id, target_buy_price, alert_enabled, added_at) VALUES (1, 50000, 1, '2026-09-01T00:00:00Z')");
    d.exec("INSERT INTO kr_corp_events (code, kind, record_date, source, fetched_at) VALUES ('000001', 'rights', '2099-01-01', 't', 't')");
    expect(셈(d)).toBe(0);
  });

  it("경로가 국내 관심 목표가를 그 수로 막는다", () => {
    const 원문 = readFileSync(join(process.cwd(), "app", "api", "cron", "intraday", "route.ts"), "utf-8");
    expect(원문).toContain(`${WATCH_RIGHTS_AFTER_ADD_SQL} AS rights_after_add`); // 보간 대신 같은 글을 박아 둔다(appSql 규칙)
    expect(원문).toMatch(/const 가드된 = new Set<number>\(\[\s*\.\.\.관심가드,\s*\.\.\.권리락뒤,/);
    expect(원문).toContain("const 권리락뒤 = rightsAfterAddGuard(watch, market);");
    expect(원문).toContain("rights_guarded_watch: 권리락뒤목표가");
  });
});

describe("거르기 (25.2238)", () => {
  it("국내·1 이상만 막고 0·null·미국은 막지 않는다", () => {
    const w = [{ stock_id: 1, rights_after_add: 1 }, { stock_id: 2, rights_after_add: 0 }, { stock_id: 3, rights_after_add: null }, { stock_id: 4 }];
    expect(rightsAfterAddGuard(w, "KR")).toEqual([1]);
    expect(rightsAfterAddGuard(w, "US")).toEqual([]);
  });

  it("넣은 날은 한국 날짜로 — UTC 전날 밤(KST 아침)에 넣고 그날이 기준일이면 넣기 전으로 본다", () => {
    const d = db();
    d.exec("INSERT INTO watchlist (stock_id, target_buy_price, alert_enabled, added_at) VALUES (1, 50000, 1, '2026-10-04T23:30:00.000Z')");
    d.exec("INSERT INTO kr_corp_events (code, kind, record_date, source, fetched_at) VALUES ('000001', 'rights', '2026-10-05', 't', 't')");
    expect(셈(d)).toBe(0);
  });
});
