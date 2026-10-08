/** 검색 목록을 서버 메모리에 두고 거른다 — 결과가 SQL 검색과 같아야 한다 (docs/infra.md 25.1032) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { STOCK_SEARCH, STOCK_SEARCH_LIST, filterSearch, stockSearchArgs, type SearchRow } from "@/lib/portfolio";

const ROOT = join(process.cwd(), "..");

function db() {
  const d = new DatabaseSync(":memory:");
  for (const f of readdirSync(join(ROOT, "migrations")).filter((x) => x.endsWith(".sql")).sort()) {
    d.exec(readFileSync(join(ROOT, "migrations", f), "utf-8"));
  }
  const 종목: Array<[number, string, string | null, string | null, string | null, string, string, string, string]> = [
    [1, "005930", "005930.KS", "삼성전자", "Samsung Electronics", "KOSPI", "KR", "KRW", "active"],
    [2, "005935", "005935.KS", "삼성전자우", null, "KOSPI", "KR", "KRW", "active"],
    [3, "AAPL", "AAPL", null, "Apple Inc.", "NASDAQ", "US", "USD", "active"],
    [4, "AA", "AA", null, "Alcoa Corp", "NYSE", "US", "USD", "active"],
    [5, "APLE", "APLE", null, "Apple Hospitality REIT", "NYSE", "US", "USD", "active"],
    [6, "008730", "008730.KS", "율촌화학", null, "KOSPI", "KR", "KRW", "active"],
    [7, "999999", null, "상폐종목", null, "KOSDAQ", "KR", "KRW", "delisted"],
    [8, "888888", null, "상폐보유", null, "KOSDAQ", "KR", "KRW", "delisted"],
    [9, "BRK.B", "BRK-B", null, "Berkshire Hathaway", "NYSE", "US", "USD", "active"],
  ];
  for (const [id, t, y, ko, en, m, c, cur, st] of 종목) {
    d.prepare(`INSERT INTO stocks (id, ticker, yahoo_symbol, name_ko, name_en, market, country, currency, status, source, fetched_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 't', 't')`).run(id, t, y, ko, en, m, c, cur, st);
  }
  d.exec(`INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)
    VALUES (8, 'buy', '2026-01-02', 100, 1, 'KRW', 1, 'none', 't', 't')`);
  return d;
}

describe("검색 목록 메모리 (25.1032)", () => {
  it("SQL 검색과 같은 결과·같은 순서", () => {
    const d = db();
    const rows = d.prepare(STOCK_SEARCH_LIST).all() as unknown as SearchRow[];
    for (const q of ["aapl", "AA", "삼성", "전자", "apple", "005", "brk", "BRK-B", "상폐", "율촌", "zzz", "a"]) {
      const sql = d.prepare(STOCK_SEARCH).all(...stockSearchArgs(q)) as Array<Record<string, unknown>>;
      const mem = filterSearch(rows, q);
      expect(mem.map((r) => r.id), q).toEqual(sql.map((r) => r.id));
      expect(mem.map((r) => r.name), q).toEqual(sql.map((r) => r.name));
    }
  });

  it("상장폐지 종목은 매매가 있을 때만", () => {
    const d = db();
    const rows = d.prepare(STOCK_SEARCH_LIST).all() as unknown as SearchRow[];
    expect(filterSearch(rows, "상폐").map((r) => r.id)).toEqual([8]);
  });
});
