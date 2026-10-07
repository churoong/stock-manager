-- ETF 매일 종가·매매 기록 (docs/infra.md 25.896, 2026-10-02 사용자 요청: "ETF 매일 종가랑 매매 기록도 되게 해줘").
--
-- 0010 은 ETF 를 stocks 와 **나눴다** — 넣으면 일일 배치·유니버스·스코어가 보통주 잣대로 판정한다. 그 이유는 그대로다.
-- 그런데 매매 기록(trades.stock_id → stocks)과 시세(prices.stock_id → stocks)는 stocks 를 가리킨다. 표를 두 벌 만들면
-- 포트폴리오·평가·장중 감시·복기가 모두 두 갈래가 된다. 그래서:
--   * 추천에 든 ETF(핵심·위성 통과)와 매매한 ETF **만** stocks 에 한 줄씩 잇는다(batch/jobs/etf_link.py)
--   * stocks.asset_type 으로 가른다. 'stock' 이 기본이라 지금 행은 그대로다. 유니버스·점수·신호·종목 찾기는 'stock' 만 본다
--   * etfs.stock_id 가 그 줄을 가리킨다. 비어 있으면 아직 잇지 않은 ETF 다
ALTER TABLE stocks ADD COLUMN asset_type TEXT NOT NULL DEFAULT 'stock';  -- stock etf

ALTER TABLE etfs ADD COLUMN stock_id INTEGER REFERENCES stocks (id);
