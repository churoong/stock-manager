/**
 * 메뉴 종목 검색의 규칙 (components/StockSearch). 화면과 떼어 테스트한다.
 */

export interface SearchHit {
  id: number;
  ticker: string;
  market: string;
  country: string;
  currency: string;
  name: string;
  /** 'etf' 면 추천·보유 ETF 를 이은 줄 (docs/infra.md 25.896) */
  asset_type?: string;
}

/** 검색 주소. 빈 칸·공백만이면 묻지 않는다(null). 서버도 40자까지만 쓴다 */
export function searchUrl(query: string): string | null {
  const q = query.trim();
  if (!q) return null;
  return `/api/stocks/search?q=${encodeURIComponent(q.slice(0, 40))}`;
}

/** 결과 한 줄: "삼성전자 005930 · KOSPI". 이름이 티커와 같으면(미국 일부) 한 번만 */
export function resultLabel(hit: SearchHit): string {
  const name = hit.name && hit.name !== hit.ticker ? `${hit.name} ` : "";
  // 이은 ETF 는 "ETF" 를 붙인다 — 미국 ETF 는 시장 칸이 거래소 이름(NYSE Arca 등)이라 주식과 구별이 안 된다 (25.896)
  const etf = hit.asset_type === "etf" && hit.market !== "ETF" ? " · ETF" : "";
  return `${name}${hit.ticker} · ${hit.market}${etf}`;
}
