/**
 * 국내 뉴스 종목 매칭 테스트 (docs/sentiment.md 1.2). 제목은 연합뉴스 스타일로 직접 썼다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { KR_TARGETS, NEWS_INSERT_KR, matchStocks, mergeFeeds } from "@/lib/newsKr";

const STOCKS = [
  { stock_id: 1, name: "삼성전자" },
  { stock_id: 2, name: "삼성전자우" },
  { stock_id: 3, name: "현대차" },
  { stock_id: 4, name: "SK하이닉스" },
  { stock_id: 5, name: "LG" },
  { stock_id: 6, name: "에스원" },
];

const ids = (title: string) => matchStocks(title, STOCKS).map((s) => s.stock_id);

describe("종목 이름 매칭", () => {
  it("조사가 붙어도 잡는다", () => {
    expect(ids("삼성전자, 3분기 영업이익 사상 최대")).toEqual([1]);
    expect(ids("현대차가 美 공장 증설…SK하이닉스는 HBM 투자")).toEqual([3, 4]);
    expect(ids("[특징주] 에스원 급등")).toEqual([6]);
  });

  it("다른 단어의 일부는 뺀다", () => {
    expect(ids("삼성전자서비스 노조 파업")).toEqual([]);
    expect(ids("현대차그룹 계열사 일제히 상승")).toEqual([]);
    expect(ids("신에스원테크 상장")).toEqual([]);
  });

  it("긴 이름이 이긴다", () => {
    expect(ids("삼성전자우 급등, 보통주와 격차 축소")).toEqual([2]);
  });

  it("두 글자 이름은 매칭하지 않는다", () => {
    expect(ids("LG, 인공지능 투자 확대")).toEqual([]);
  });

  it("한 제목에 같은 종목이 두 번 나와도 한 번", () => {
    expect(ids("삼성전자 주가…삼성전자 외국인 순매수")).toEqual([1]);
  });
});

describe("피드 합치기", () => {
  const feed = (url: string) => `<rss><channel><item><title><![CDATA[제목]]></title><link>${url}</link>
    <pubDate>Thu, 17 Sep 2026 17:01:52 +0900</pubDate></item></channel></rss>`;
  it("같은 주소는 하나만, 시각은 UTC 로", () => {
    const items = mergeFeeds([feed("https://www.yna.co.kr/view/A1"), feed("https://www.yna.co.kr/view/A1"), feed("https://www.yna.co.kr/view/A2")]);
    expect(items.map((i) => i.url)).toEqual(["https://www.yna.co.kr/view/A1", "https://www.yna.co.kr/view/A2"]);
    expect(items[0].published_at).toBe("2026-09-17T08:01:52.000Z");
  });
});

describe("질의 (실제 스키마)", () => {
  const db = new DatabaseSync(":memory:");
  const dir = join(process.cwd(), "..", "migrations");
  for (const file of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) db.exec(readFileSync(join(dir, file), "utf-8"));
  db.exec(`
    INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at) VALUES
      (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't'),
      (2, '012750', 'KOSPI', 'KR', '에스원', 'KRW', 'active', 't', 't'),
      (3, 'AAPL', 'NASDAQ', 'US', NULL, 'USD', 'active', 't', 't');
    INSERT INTO watchlist (stock_id, added_at, alert_enabled) VALUES (2, 't', 1), (3, 't', 1);
    INSERT INTO news_targets (market, stock_id, rank, reason, built_at) VALUES ('KR', 1, 1, 'score', 't');
  `);

  it("국내 대상만, 한글 이름이 있는 종목만", () => {
    const rows = db.prepare(KR_TARGETS).all() as Array<{ stock_id: number; name: string }>;
    expect(rows.map((r) => r.stock_id).sort()).toEqual([1, 2]);
  });

  it("같은 종목·주소는 한 번, 다른 종목이면 따로", () => {
    const ins = db.prepare(NEWS_INSERT_KR);
    expect(ins.run(1, "삼성전자·에스원", "https://y/1", "2026-09-17T00:00:00Z", "now").changes).toBe(1);
    expect(ins.run(1, "삼성전자·에스원", "https://y/1", "2026-09-17T00:00:00Z", "now").changes).toBe(0);
    expect(ins.run(2, "삼성전자·에스원", "https://y/1", "2026-09-17T00:00:00Z", "now").changes).toBe(1);
  });
});
