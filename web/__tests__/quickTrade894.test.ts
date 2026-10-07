/**
 * 그 자리에서 매매 입력, 적립 탭의 현재가 (docs/infra.md 25.894, 2026-10-02 사용자 요청:
 * "종목상세, 추천, 적립에서 매매입력을 바로 할 수 있게해줘. 그리고 왜 적립탭에는 현재가 등이 안나와?").
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { buildAccumulationPassedQuery } from "@/lib/accumulation";

const 읽기 = (f: string) => readFileSync(f, "utf-8");

describe("그 자리에서 매매 입력", () => {
  it("종목 상세·추천 카드·적립 종목이 같은 빠른 입력을 쓴다", () => {
    for (const f of ["components/StockDetail.tsx", "components/RecommendList.tsx", "components/AccumulationStocks.tsx"]) {
      expect(읽기(f), f).toMatch(/<QuickTrade\b/);
    }
  });

  it("빠른 입력은 포트폴리오와 **같은 폼**이다 — 저장 규칙(중복·단위 확인)이 한 곳", () => {
    const quick = 읽기("components/QuickTrade.tsx");
    expect(quick).toContain('import { TradeForm } from "@/components/PortfolioView"');
    const view = 읽기("components/PortfolioView.tsx");
    expect(view).toMatch(/export function TradeForm\(/);
    expect(view).toContain("<TradeForm onSaved={onChanged} onReload={onReload} />");
  });

  it("체결가·수량은 미리 채우지 않는다 — trades 는 사용자 입력값만 (CLAUDE.md)", () => {
    const quick = 읽기("components/QuickTrade.tsx");
    expect(quick).not.toMatch(/defaultPrice|buy_zone|close=/);
  });

  it("종목이 정해진 폼은 '바꾸기' 를 띄우지 않고, 포트폴리오 폼만 #trade-form 을 갖는다", () => {
    const view = 읽기("components/PortfolioView.tsx");
    expect(view).toContain('{fixedStock ? null : <button type="button"');
    expect(view).toContain('id={fixedStock ? undefined : "trade-form"}');
  });
});

describe("적립 탭의 가격", () => {
  it("적립 종목은 가장 새 종가와 날짜를 종목마다 색인으로 한 행만 읽는다", () => {
    const { sql } = buildAccumulationPassedQuery("KR");
    expect(sql).toContain("px.close, px.date AS price_date");
    expect(sql).toContain("AND px.date = (SELECT MAX(p5.date) FROM prices p5 WHERE p5.stock_id = s.id)");
    expect(읽기("components/AccumulationStocks.tsx")).toContain("종가)</span>");
  });

  it("ETF 는 날마다 시세를 받지 않는다 — '판정 때 종가' 로 날짜를 붙여 현재가로 읽히지 않게", () => {
    for (const f of ["components/EtfList.tsx", "components/SatelliteList.tsx"]) {
      const src = 읽기(f);
      expect(src, f).toContain("판정 때 종가");
      expect(src, f).toMatch(/row\.country === "US" \? `\$\{row\.as_of_date\} 판정` : row\.as_of_date/); // 미국은 판정 전 거래일 종가 (25.921)
    }
  });
});

describe("매매 입력 단추 배치 (25.895, 사용자: 줄바꿈이 돼서 보기 안 좋다)", () => {
  it("단추 글자는 줄을 바꾸지 않고, 폼은 다음 줄 전체를 쓴다", () => {
    const quick = 읽기("components/QuickTrade.tsx");
    expect(quick).toMatch(/whitespace-nowrap/);
    expect(quick).toMatch(/basis-full/);
  });

  it("세 화면 모두 다른 단추와 같은 줄(flex-wrap) 안에 둔다", () => {
    for (const f of ["components/StockDetail.tsx", "components/RecommendList.tsx", "components/AccumulationStocks.tsx"]) {
      const src = 읽기(f);
      const i = src.indexOf("<QuickTrade");
      const 앞 = src.slice(Math.max(0, i - 1500), i);
      expect(앞.lastIndexOf("flex flex-wrap"), f).toBeGreaterThan(-1);
    }
  });
});
