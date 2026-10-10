/** 미국 뉴스: 피드 단위 티커 거르기, 클래스주 표기, 주소 정규화 (docs/infra.md 25.546, 감사 재현). */

import { describe, expect, it } from "vitest";
import { filterForSymbol, normalizeUrl, titleMentions, type FeedItem } from "@/lib/news";

const 기사 = (title: string, tickers: string[], url = `https://x/${title}`): FeedItem => ({
  title, url, published_at: "2026-09-28T00:00:00.000Z", publisher: null, tickers,
});

describe("미국 뉴스 거르기 (25.546)", () => {
  it("티커가 달린 피드에 섞인 티커 없는 기사는 뺀다", () => {
    const 피드 = [기사("Apple beats", ["AAPL"]), 기사("Pre-Market Movers: Tesla plunges on recall", [])];
    expect(filterForSymbol(피드, "AAPL").map((i) => i.title)).toEqual(["Apple beats"]);
  });

  it("티커 목록이 전혀 없는 피드는 전부 남긴다", () => {
    const 피드 = [기사("a", []), 기사("b", [])];
    expect(filterForSymbol(피드, "AAPL")).toHaveLength(2);
  });

  it("클래스주는 BRK.B 와 BRK-B 를 같은 것으로 본다", () => {
    expect(filterForSymbol([기사("Berkshire", ["BRK.B"])], "BRK-B")).toHaveLength(1);
  });

  it("http·https·조각이 다른 같은 기사는 한 주소", () => {
    expect(normalizeUrl("http://a.com/x?utm=1#top")).toBe("https://a.com/x");
    expect(normalizeUrl("https://a.com/x")).toBe("https://a.com/x");
  });
});

/** 티커가 여럿 달린 모음 기사가 제목 전체 점수로 모든 종목에 붙던 것 (docs/infra.md 25.745, 감사 재현) */
describe("모음 기사 거르기 (25.745)", () => {
  it("제목이 그 종목을 부르지 않으면 뺀다", () => {
    const 모음 = 기사("Tesla plunges on recall; Microsoft steady", ["AAPL", "TSLA", "MSFT"]);
    expect(filterForSymbol([모음], "AAPL", "Apple Inc.")).toHaveLength(0);
    expect(filterForSymbol([모음], "TSLA", "Tesla, Inc.")).toHaveLength(1);
    expect(filterForSymbol([모음], "MSFT", "Microsoft Corporation")).toHaveLength(1);
  });
  it("티커가 하나면 제목을 보지 않는다", () => {
    expect(filterForSymbol([기사("3 Stocks to Buy Now", ["AAPL"])], "AAPL", "Apple Inc.")).toHaveLength(1);
  });
  it("티커 표기·흔한 첫 낱말", () => {
    expect(titleMentions("Why $NVDA and AMD rallied", "NVDA", "NVIDIA Corporation")).toBe(true);
    expect(titleMentions("Berkshire (BRK.B) adds stake", "BRK-B", "Berkshire Hathaway")).toBe(true);
    expect(titleMentions("General Electric beats", "GM", "General Motors Company")).toBe(false);
    expect(titleMentions("General Motors recalls trucks", "GM", "General Motors Company")).toBe(true);
    expect(titleMentions("Apples and oranges", "AAPL", "Apple Inc.")).toBe(true); // 복수형은 허용 — 과하게 남는 쪽
  });
});

/** 25.745 교차검증 반영 (docs/infra.md 25.748) */
describe("모음 기사 거르기 보완 (25.748)", () => {
  it("한 회사의 두 클래스주가 달린 기사는 제목을 보지 않는다", () => {
    expect(filterForSymbol([기사("Google hit with antitrust ruling", ["GOOG", "GOOGL"])], "GOOG", "Alphabet Inc. - Class C")).toHaveLength(1);
  });
  it("티커 셋 이상이면 여전히 제목을 본다", () => {
    expect(filterForSymbol([기사("Tesla plunges; Microsoft steady", ["AAPL", "TSLA", "MSFT"])], "AAPL", "Apple Inc.")).toHaveLength(0);
  });
  it("문장 끝 마침표·앞의 the·짧은 첫 낱말", () => {
    expect(titleMentions("Investors pile into Apple.", "AAPL", "Apple Inc.")).toBe(true);
    expect(titleMentions("Why I'm buying NVDA.", "NVDA", "NVIDIA Corporation")).toBe(true);
    expect(titleMentions("Coca-Cola beats", "KO", "The Coca-Cola Company")).toBe(true);
    expect(titleMentions("JPMorgan beats estimates", "JPM", "JP Morgan Chase & Co. Common Stock")).toBe(true);
    expect(titleMentions("JP Morgan beats estimates", "JPM", "JP Morgan Chase & Co. Common Stock")).toBe(true);
    expect(titleMentions("Berkshire (BRK.B) adds stake", "BRK-B", "Berkshire Hathaway")).toBe(true);
  });
});

/** 25.748 교차검증 반영 (docs/infra.md 25.750) */
describe("the 로 시작하는 이름 (25.750)", () => {
  it("the 를 뗀 이름은 두 낱말로 부른다", () => {
    expect(titleMentions("Trade war hits stocks", "TTD", "The Trade Desk, Inc.")).toBe(false);
    expect(titleMentions("Trade Desk soars on earnings", "TTD", "The Trade Desk, Inc.")).toBe(true);
    expect(titleMentions("Coca-Cola beats", "KO", "The Coca-Cola Company")).toBe(true);
    expect(titleMentions("Home sales slump", "HD", "Home Depot, Inc. (The)")).toBe(false);
    expect(titleMentions("Boeing jets delayed", "BA", "The Boeing Company")).toBe(true);
  });
});

/** 25.750 교차검증 반영 (docs/infra.md 25.755) */
describe("Bank of 이름 (25.755)", () => {
  it("of 뒤 낱말까지 본다", () => {
    expect(titleMentions("Bank of America beats estimates", "BAC", "Bank of America Corporation")).toBe(true);
    expect(titleMentions("Bank of Japan hikes, banks fall", "BAC", "Bank of America Corporation")).toBe(false);
    expect(titleMentions("Bank of America, Wells slide", "BK", "The Bank of New York Mellon Corporation")).toBe(false);
  });
});

describe("영어 낱말과 같은 티커·이름 (docs/infra.md 25.1098, 감사 재현)", () => {
  it("낱말 티커는 대문자 그대로 나올 때만, 한 글자 티커는 $A·(A) 꼴만", () => {
    expect(titleMentions("Tesla, Apple, Microsoft all slide", "ALL", "Allstate Corporation")).toBe(false);
    expect(titleMentions("Is now the time to buy chips?", "NOW", "ServiceNow, Inc.")).toBe(false);
    expect(titleMentions("A look at Apple's margins", "A", "Agilent Technologies")).toBe(false);
    expect(titleMentions("Why $NOW and ORCL rallied", "NOW", "ServiceNow, Inc.")).toBe(true);
    expect(titleMentions("Allstate (ALL) beats", "ALL", "Allstate Corporation")).toBe(true);
    expect(titleMentions("Agilent (A) raises guidance", "A", "Agilent Technologies")).toBe(true);
    expect(titleMentions("ServiceNow tops estimates", "NOW", "ServiceNow, Inc.")).toBe(true); // 이름으로는 그대로
    // 낱말이 아닌 티커는 예전처럼 대소문자를 가리지 않는다
    expect(titleMentions("why aapl fell", "AAPL", "Apple Inc.")).toBe(true);
  });

  it("흔한 첫 낱말 이름은 두 낱말을 본다", () => {
    expect(titleMentions("Analysts raise price targets on chips", "TGT", "Target Corporation")).toBe(false);
    expect(titleMentions("Best stocks to buy now", "BBY", "Best Buy Co., Inc.")).toBe(false);
    expect(titleMentions("Dollar slides as Fed signals cut", "DG", "Dollar General Corporation")).toBe(false);
    expect(titleMentions("Best Buy cuts outlook", "BBY", "Best Buy Co., Inc.")).toBe(true);
    expect(titleMentions("Dollar General shares jump", "DG", "Dollar General Corporation")).toBe(true);
  });

  it("모음 기사에서 낱말 티커 종목에 남의 제목이 붙지 않는다", () => {
    const 피드 = [기사("Tesla, Apple, Microsoft all slide", ["TSLA", "AAPL", "MSFT", "ALL"])];
    expect(filterForSymbol(피드, "ALL", "Allstate Corporation")).toHaveLength(0);
  });
});
