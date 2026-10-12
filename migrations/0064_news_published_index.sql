-- 감성 일일 질의가 날짜 범위로 기사를 고르게 (docs/infra.md 25.1179). `news` 에는 (stock_id, published_at) 색인뿐이라
-- "최근 61일 기사" 를 고르는 세 질의(채점 대상·집계·미채점 수)가 news·article_sentiments 를 통째로 훑었다 — 아무도 자르지
-- 않는 표라 이력이 쌓일수록 매일 읽는 양이 늘었다(감사 재현: 이력 61일 → 365일이면 단계 1,993 → 10,219)
CREATE INDEX IF NOT EXISTS idx_news_published ON news (published_at);
