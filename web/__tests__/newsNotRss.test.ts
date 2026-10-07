/** 200 으로 온 봇 차단 HTML 을 "기사 0건" 과 가른다 (docs/infra.md 25.640, 감사). */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { looksLikeFeed } from "@/lib/news";

describe("looksLikeFeed", () => {
  it("RSS·Atom 은 기사가 0건이어도 피드다", () => {
    expect(looksLikeFeed('<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>')).toBe(true);
    expect(looksLikeFeed('<feed xmlns="http://www.w3.org/2005/Atom"></feed>')).toBe(true);
  });

  it("차단 페이지·빈 본문은 피드가 아니다", () => {
    expect(looksLikeFeed("<!DOCTYPE html><html><body>Access Denied</body></html>")).toBe(false);
    expect(looksLikeFeed("")).toBe(false);
  });

  it("두 경로가 그 판정을 쓴다", () => {
    for (const f of ["app/api/cron/news/route.ts", "app/api/cron/news-kr/route.ts"]) {
      expect(readFileSync(f, "utf8")).toContain("looksLikeFeed(text)");
    }
  });
});
