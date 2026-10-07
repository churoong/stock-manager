/**
 * 자주 도는 크론 질의가 종목 표 전체를 훑지 않는가 (docs/infra.md 25.857, 감사).
 *
 * 통계 없는 SQLite(D1)는 `JOIN` 의 순서를 스스로 고른다. 후보 표(수백 행) 대신 stocks(수천 행)를 바깥 고리로 고르면
 * 호출마다 종목 수만큼 읽는다 — 1분마다 도는 미국 뉴스 크론 하나로 하루 약 400만 행, D1 읽기 한도의 80% 였다.
 * `CROSS JOIN` 은 왼쪽을 바깥으로 고정한다. 여기서는 migrations/ 를 적용한 SQLite 에 EXPLAIN QUERY PLAN 을 돌려
 * 바깥 고리가 `SCAN s`(stocks 전체)가 아닌지 본다.
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeAll, describe, expect, it } from "vitest";
import { NEXT_TARGET } from "@/lib/news";
import { KR_ALIASES, KR_TARGETS } from "@/lib/newsKr";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

beforeAll(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
});

function 계획(sql: string, n: number): string[] {
  return (db.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...Array(n).fill("x")) as Array<{ detail: string }>).map((r) => r.detail);
}

const 장중관심 = (() => {
  const 글 = readFileSync("app/api/cron/intraday/route.ts", "utf-8");
  const m = 글.match(/`(SELECT w\.stock_id[\s\S]*?FROM watchlist w[\s\S]*?)`/);
  if (!m) throw new Error("장중 관심 종목 질의를 찾지 못했다");
  return m[1];
})();

describe("크론 질의가 stocks 를 통째로 훑지 않는다 (25.857)", () => {
  it.each([
    ["미국 뉴스 다음 종목 (1분마다)", NEXT_TARGET, 2],
    ["국내 뉴스 대상 (1시간마다)", KR_TARGETS, 0],
    ["국내 뉴스 별칭 (1시간마다)", KR_ALIASES, 0],
    ["장중 관심 종목 (5분마다)", 장중관심, 1],
  ])("%s", (_이름, sql, n) => {
    const 줄 = 계획(sql as string, n as number);
    expect(줄.some((d) => /^SCAN s\b/.test(d))).toBe(false);
    expect(줄.some((d) => /^SEARCH s USING INTEGER PRIMARY KEY/.test(d))).toBe(true);
  });
});

describe("판정 버전 하위 질의가 행마다 다시 돌지 않는다 (25.858)", () => {
  it("ETF·위성 ETF·적립 종목 질의에 CORRELATED 하위 질의가 없다", async () => {
    const { buildPickedQuery, buildExcludedQuery } = await import("@/lib/etf");
    const { buildSatellitePassedQuery, buildSatelliteExcludedQuery } = await import("@/lib/etfSatellite");
    const { buildAccumulationPassedQuery, buildAccumulationFunnelQuery } = await import("@/lib/accumulation");
    const 질의들 = [
      buildPickedQuery(), buildExcludedQuery("KR"), buildSatellitePassedQuery("KR"), buildSatelliteExcludedQuery("US"),
      buildAccumulationPassedQuery("KR"), buildAccumulationFunnelQuery("US"),
    ];
    for (const q of 질의들) {
      const 줄 = (db.prepare(`EXPLAIN QUERY PLAN ${q.sql}`).all(...(q.args as never[])) as Array<{ detail: string }>).map((r) => r.detail);
      // 적립 종목의 **가장 새 종가**(25.894)는 종목마다 색인 한 번 찾는 상관 하위 질의다 — 추천·종목 찾기와 같은 모양.
      // 그것 하나만 허용하고, 그 하위 질의가 (stock_id, date) 색인을 타는지 본다
      const 허용 = q.sql.includes("px.date = (SELECT MAX(p5.date)") ? 1 : 0;
      expect(줄.filter((d) => d.includes("CORRELATED")).length, q.sql.slice(0, 80)).toBe(허용);
      if (허용) expect(줄.some((d) => /^SEARCH p5 USING (COVERING )?INDEX/.test(d))).toBe(true);
    }
  });
});

describe("종목 찾기가 가격 표를 통째로 훑지 않는다 (25.859)", () => {
  it("종가는 종목별 주키로 찾는다", async () => {
    const { buildQuery, defaultFilters } = await import("@/lib/screener");
    const q = buildQuery(defaultFilters("KR"));
    const 줄 = (db.prepare(`EXPLAIN QUERY PLAN ${q.sql}`).all(...(q.args as never[])) as Array<{ detail: string }>).map((r) => r.detail);
    expect(줄.filter((d) => /^SCAN (prices|p\b|p2\b)/.test(d))).toEqual([]);
    expect(줄.filter((d) => /^SCAN f\b/.test(d))).toEqual([]); // 재무 표 전체도 아니다
  });
});

describe("신호 0건 진단의 거래일 수 (25.859)", () => {
  const 글 = readFileSync("app/api/recommend/route.ts", "utf-8");
  const 몸 = 글.slice(글.indexOf("async function 재료읽기"), 글.indexOf("export async function POST"));
  const sql = [...몸.slice(몸.indexOf("execute("), 몸.indexOf("[country")).matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1]).join("");
  const 인자수 = (몸.match(/\[country(, country)*\]/)?.[0].match(/country/g) ?? []).length;

  it("자리표시자와 인자 수가 맞고, 날짜를 한 칸씩 세어 결과가 같다", () => {
    expect((sql.match(/\?/g) ?? []).length).toBe(인자수);
    const t = new DatabaseSync(":memory:");
    for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) t.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
    t.exec(`INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at) VALUES
      (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't'), (2, 'B', 'NYSE', 'US', 'USD', 'active', 't', 't')`);
    const 가격 = t.prepare("INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (?, ?, 1, 'KRW', 't', 't')");
    for (const d of ["2026-09-01", "2026-09-02", "2026-09-03"]) 가격.run(1, d);
    가격.run(2, "2026-09-02");
    const 행 = (c: string) => t.prepare(sql).get(...Array(인자수).fill(c)) as Record<string, number>;
    expect(행("KR").price_days).toBe(3);
    expect(행("US").price_days).toBe(1);
    t.exec("DELETE FROM prices WHERE stock_id = 2");
    expect(행("US").price_days).toBe(0);
  });

  it("그 나라 가격 행을 통째로 읽지 않는다", () => {
    const 줄 = (db.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...Array(인자수).fill("KR")) as Array<{ detail: string }>).map((r) => r.detail);
    expect(줄.filter((d) => /^SCAN p\b/.test(d))).toEqual([]); // 예전: SCAN p USING INDEX idx_prices_date (가격 행 전부)
  });

  it("날짜를 걷지 않고, 종목마다 판정 문턱(RISK_DAYS)에서 멈춘다 (25.900)", async () => {
    // 날짜 걷기는 미국 탭에서 날마다 그날 국내 행을 지나쳤다(25.899 와 같은 원인). 걷기를 되살리면 이 검사가 깨진다
    const { RISK_DAYS } = await import("@/lib/whyEmpty");
    expect(sql).not.toContain("WITH RECURSIVE");
    expect(sql).toContain(`LIMIT ${RISK_DAYS}))`);
    const 줄 = (db.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...Array(인자수).fill("US")) as Array<{ detail: string }>).map((r) => r.detail);
    expect(줄.some((d) => /^SEARCH p USING (COVERING )?INDEX \S+ \(stock_id=\?/.test(d))).toBe(true);
  });

  it("앞 20종목 중 가장 긴 이력을 200에서 자른다", () => {
    const t = new DatabaseSync(":memory:");
    for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) t.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
    t.exec(`INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at) VALUES
      (1, 'A', 'NYSE', 'US', 'USD', 'active', 't', 't'), (2, 'B', 'NYSE', 'US', 'USD', 'active', 't', 't')`);
    const 가격 = t.prepare("INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (?, ?, 1, 'USD', 't', 't')");
    for (let i = 0; i < 250; i++) 가격.run(2, new Date(Date.UTC(2025, 0, 1 + i)).toISOString().slice(0, 10));
    가격.run(1, "2025-01-01");
    expect((t.prepare(sql).get(...Array(인자수).fill("US")) as Record<string, number>).price_days).toBe(200);
  });
});

describe("상태 화면·무응답 감시의 작은 질의도 표를 통째로 읽지 않는다 (25.862)", () => {
  it("최근 배치 기록은 id 역순, 거래일 달력은 주키로", async () => {
    const { RECENT_BATCH_RUNS, SESSIONS_AROUND } = await import("@/lib/health");
    const 배치 = (db.prepare(`EXPLAIN QUERY PLAN ${RECENT_BATCH_RUNS}`).all() as Array<{ detail: string }>).map((r) => r.detail);
    expect(배치.some((d) => d.includes("USE TEMP B-TREE FOR ORDER BY"))).toBe(false);
    const 달력 = (db.prepare(`EXPLAIN QUERY PLAN ${SESSIONS_AROUND}`).all("2026-10-01") as Array<{ detail: string }>).map((r) => r.detail);
    expect(달력.some((d) => /^SEARCH market_sessions/.test(d))).toBe(true);
  });
});
