/** 국내 피드를 받았는데 읽힌 기사가 0건이면 실패로 남긴다 (docs/infra.md 25.836) */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { parseRss } from "@/lib/news";

describe("국내 뉴스 크론 — 기사 0건", () => {
  it("형식이 바뀐 피드는 항목을 버린다 — 그래서 0건을 실패로 본다", () => {
    const 바뀐 = `<?xml version="1.0"?><rss><channel><item><title>삼성전자 호조</title><link>https://y/1</link><pubDate>어제</pubDate></item></channel></rss>`;
    expect(parseRss(바뀐)).toEqual([]);
    const 경로 = readFileSync("app/api/cron/news-kr/route.ts", "utf-8");
    expect(경로).toContain("const outcome = newsKrOutcome(xmls.length, items.length);");
    expect(경로).toContain('recordHeartbeat("news", "KR", now, outcome,');
  });
});

describe("결과 판정 함수 (25.838)", () => {
  it("피드가 있고 기사가 있을 때만 정상", async () => {
    const { newsKrOutcome } = await import("@/lib/newsKr");
    expect(newsKrOutcome(3, 120)).toBe("checked");
    expect(newsKrOutcome(3, 0)).toBe("error");
    expect(newsKrOutcome(0, 0)).toBe("error");
  });
});
