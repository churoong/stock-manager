/**
 * 뉴스 수집 테스트 (docs/sentiment.md 1장). 파서는 2026-09-17 나스닥 실제 응답 조각으로, 질의는 실제 스키마로.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import {
  FETCH_LOG_UPSERT,
  NASDAQ_CRAWL_DELAY_SEC,
  NEWS_INSERT,
  NEXT_TARGET,
  REFETCH_AFTER_HOURS,
  filterForSymbol,
  parseRss,
  refetchCutoff,
  retryCutoff,
} from "@/lib/news";

const SAMPLE = `<?xml version="1.0" encoding="utf-8"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:nasdaq="http://nasdaq.com/reference/feeds/1.0" version="2.0">
 <channel>
  <item>
   <title>Apple Is Considering Something It Hasn&#39;t Done in 15 Years. Here&#39;s What Investors Need to Know.</title>
   <link>https://www.nasdaq.com/articles/apple-considering-something?time=1</link>
   <description>본문 요약은 저장하지 않는다</description>
   <pubDate>Wed, 16 Sep 2026 17:59:50 +0000</pubDate>
   <dc:creator>The Motley Fool</dc:creator>
   <nasdaq:tickers>AAPL,AAPL,NVDA</nasdaq:tickers>
  </item>
  <item>
   <title><![CDATA[31% of Berkshire's Portfolio & 2 AI Stocks]]></title>
   <link>https://www.nasdaq.com/articles/berkshire</link>
   <pubDate>Wed, 16 Sep 2026 16:50:00 +0000</pubDate>
   <dc:creator>The Motley Fool</dc:creator>
   <nasdaq:tickers>GOOGL,GOOG</nasdaq:tickers>
  </item>
  <item>
   <title>No date</title>
   <link>https://www.nasdaq.com/articles/nodate</link>
  </item>
 </channel>
</rss>`;

describe("RSS 파서", () => {
  it("제목·주소·시각·매체·티커를 읽고, 시각 없는 항목은 버린다", () => {
    const items = parseRss(SAMPLE);
    expect(items).toHaveLength(2);
    expect(items[0]).toEqual({
      title: "Apple Is Considering Something It Hasn't Done in 15 Years. Here's What Investors Need to Know.",
      url: "https://www.nasdaq.com/articles/apple-considering-something",
      published_at: "2026-09-16T17:59:50.000Z",
      publisher: "The Motley Fool",
      tickers: ["AAPL", "NVDA"],
    });
    expect(items[1].title).toBe("31% of Berkshire's Portfolio & 2 AI Stocks");
  });

  it("그 종목이 관련 티커에 든 기사만 남긴다", () => {
    const items = parseRss(SAMPLE);
    expect(filterForSymbol(items, "AAPL", "Apple Inc.").map((i) => i.url)).toEqual(["https://www.nasdaq.com/articles/apple-considering-something"]);
    // "31% of Berkshire's Portfolio & 2 AI Stocks" 는 GOOGL·GOOG(한 회사의 두 클래스) 두 티커라 제목을 보지 않고 남긴다 (25.748)
    expect(filterForSymbol(items, "GOOG", "Alphabet Inc.")).toHaveLength(1);
    expect(filterForSymbol([{ ...items[0], tickers: [] }], "ZZZ")).toHaveLength(1);
  });
});

describe("질의 (실제 스키마)", () => {
  const db = new DatabaseSync(":memory:");
  const dir = join(process.cwd(), "..", "migrations");
  for (const file of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) db.exec(readFileSync(join(dir, file), "utf-8"));
  db.exec(`
    INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at, yahoo_symbol) VALUES
      (1, 'AAPL', 'NASDAQ', 'US', 'Apple', 'USD', 'active', 't', 't', 'AAPL'),
      (2, 'MSFT', 'NASDAQ', 'US', 'Microsoft', 'USD', 'active', 't', 't', 'MSFT'),
      (3, 'KO', 'NYSE', 'US', 'Coca-Cola', 'USD', 'active', 't', 't', 'KO'),
      (4, '005930', 'KOSPI', 'KR', 'Samsung', 'KRW', 'active', 't', 't', '005930.KS');
    INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date, updated_at)
      VALUES (1, 1, 'USD', 1, 1, 1, 1, '2026-01-01', 't'), (4, 1, 'KRW', 1, 1, 1, 1, '2026-01-01', 't');
    INSERT INTO watchlist (stock_id, added_at, alert_enabled) VALUES (2, 't', 1);
    -- 점수 상위 후보는 배치가 미리 골라 둔다 (docs/infra.md 24절). 웹은 이 표만 읽는다
    INSERT INTO news_targets (market, stock_id, rank, reason, built_at) VALUES ('US', 3, 1, 'score', 't');
  `);
  const now = new Date("2026-09-17T08:00:00Z");

  it("보유·관심·배치가 고른 후보를 합치고, 국내 종목은 빼고, 안 받은 것부터 준다", () => {
    const pick = () =>
      db.prepare(NEXT_TARGET).get(refetchCutoff(now), retryCutoff(now)) as { stock_id: number } | undefined;
    expect(pick()?.stock_id).toBe(1);
    db.prepare(FETCH_LOG_UPSERT).run(1, "2026-09-17T07:00:00Z", "ok", 15, 3);
    expect(pick()?.stock_id).toBe(2);
    db.prepare(FETCH_LOG_UPSERT).run(2, "2026-09-17T07:30:00Z", "ok", 0, 0);
    expect(pick()?.stock_id).toBe(3);
    db.prepare(FETCH_LOG_UPSERT).run(3, "2026-09-17T07:40:00Z", "http:403", 0, 0);
    expect(pick()).toBeUndefined(); // 성공 둘은 20시간 안, 실패 하나는 1시간 안
    db.prepare(FETCH_LOG_UPSERT).run(3, "2026-09-17T06:59:00Z", "fetch-error:fetch failed", 0, 0);
    expect(pick()?.stock_id).toBe(3); // 실패는 1시간 지나면 다시
    db.prepare(FETCH_LOG_UPSERT).run(3, "2026-09-17T07:40:00Z", "ok", 0, 0);
    db.prepare(FETCH_LOG_UPSERT).run(1, "2026-09-16T07:00:00Z", "ok", 15, 3);
    expect(pick()?.stock_id).toBe(1); // 하루 지난 것부터 다시
  });

  it("보유 종목이 다시 받을 때가 되면 한 번도 안 받은 후보보다 먼저 준다 (25.909)", () => {
    const pick = () =>
      db.prepare(NEXT_TARGET).get(refetchCutoff(now), retryCutoff(now)) as { stock_id: number } | undefined;
    db.exec("DELETE FROM news_fetch_log");
    db.prepare(FETCH_LOG_UPSERT).run(1, "2026-09-16T07:00:00Z", "ok", 15, 3); // 보유 — 25시간 전
    db.prepare(FETCH_LOG_UPSERT).run(2, "2026-09-16T07:00:00Z", "ok", 15, 3); // 관심 — 25시간 전
    expect(pick()?.stock_id).toBe(1); // 후보 3 은 한 번도 안 받았지만 보유가 먼저
    db.prepare(FETCH_LOG_UPSERT).run(1, "2026-09-17T07:59:00Z", "ok", 15, 3);
    expect(pick()?.stock_id).toBe(2); // 그다음 관심
    db.prepare(FETCH_LOG_UPSERT).run(2, "2026-09-17T07:59:00Z", "ok", 15, 3);
    expect(pick()?.stock_id).toBe(3);
    db.exec("DELETE FROM news_fetch_log");
  });

  it("큰 표(scores·prices)를 읽지 않는다 — 1분마다 도는 경로라 읽기 한도를 태운다", () => {
    for (const table of ["scores", "prices", "factors", "signals"]) {
      expect(NEXT_TARGET).not.toContain(` ${table} `);
    }
  });

  it("다른 시장의 후보는 섞이지 않는다", () => {
    db.exec("INSERT INTO news_targets (market, stock_id, rank, reason, built_at) VALUES ('KR', 4, 1, 'score', 't')");
    const rows = db.prepare(NEXT_TARGET).all("2099-01-01", "2099-01-01") as Array<{ stock_id: number }>;
    expect(rows.map((r) => r.stock_id)).not.toContain(4);
  });

  it("같은 종목·주소는 한 번만 저장된다", () => {
    const ins = db.prepare(NEWS_INSERT);
    expect(ins.run(1, "t", "https://x/a", "2026-09-16T00:00:00Z", "p", "now").changes).toBe(1);
    expect(ins.run(1, "t", "https://x/a", "2026-09-16T00:00:00Z", "p", "now").changes).toBe(0);
    expect(ins.run(2, "t", "https://x/a", "2026-09-16T00:00:00Z", "p", "now").changes).toBe(1);
  });
});

/**
 * 나스닥 크롤 예절 (2026-09-21, docs/infra.md 25.49).
 *
 * `NASDAQ_CRAWL_DELAY_SEC = 30` 은 나스닥 `robots.txt` 의 `Crawl-delay` 다(2026-09-17 확인).
 * 그런데 코드 어디서도 **그 값을 기다리지 않는다.** 대신 규칙을 다른 방식으로 지킨다 —
 * **한 호출에 한 종목만** 받고, 그 경로는 1분에 한 번 불린다. 그래서 종목 사이 간격이
 * 저절로 30초를 넘는다.
 *
 * 즉 이 상수는 **`LIMIT 1` 이 왜 거기 있는지**를 적어 둔 것이다. 누군가 "한 번에 여러 종목을
 * 받으면 빠르겠다" 며 그 `LIMIT` 을 늘리면 **예절을 어기게 되고**, 상수는 여전히 30 이라고
 * 적혀 있어 아무도 눈치채지 못한다. 그 연결을 여기서 잡는다.
 */
describe("한 호출에 한 종목", () => {
  it("대상 질의가 한 종목만 고른다", () => {
    // 늘리려면 진짜로 기다리는 코드를 먼저 넣어야 한다
    expect(NEXT_TARGET.trimEnd().endsWith("LIMIT 1")).toBe(true);
  });

  it("크롤 지연 값이 남아 있다", () => {
    // 값이 사라지면 왜 한 종목씩인지 아무도 모르게 된다
    expect(NASDAQ_CRAWL_DELAY_SEC).toBeGreaterThanOrEqual(30);
  });

  it("같은 종목을 하루 한 번보다 자주 받지 않는다", () => {
    expect(REFETCH_AFTER_HOURS).toBeGreaterThanOrEqual(20);
  });
});
