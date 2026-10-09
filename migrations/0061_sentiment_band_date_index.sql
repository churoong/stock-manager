-- 날짜로 고르는 질의에 색인 (docs/infra.md 25.1081). 두 표 모두 (stock_id, as_of_date) 색인뿐이라
-- "그 나라의 한 주 안 감성"·"최신 밴드" 를 고르는 종목 분석 의견(verdicts) 질의가 표 전체를 시장마다 날마다 훑었다.
-- 두 표 모두 날마다·주마다 자라므로 비용도 함께 자란다.
--   verdicts.SENTIMENT_GAP_SQL  sentiment_scores 의 as_of_date >= 한 주 전
--   verdicts.BAND_SQL           valuation_bands 의 as_of_date = 최신, metric = 'PBR'
CREATE INDEX IF NOT EXISTS idx_sentiment_scores_date ON sentiment_scores (as_of_date);
CREATE INDEX IF NOT EXISTS idx_valuation_bands_date ON valuation_bands (as_of_date, metric);
