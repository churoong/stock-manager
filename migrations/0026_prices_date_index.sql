-- 날짜만으로 가격을 찾는 인덱스 (docs/health.md 4장의 "데이터 신선도").
--
-- 왜 필요한가. 시스템 상태 화면이 시장별 최신 시세일을 묻는다:
--   SELECT MAX(p.date) FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE s.country = 'KR'
-- 기존 인덱스는 (stock_id, date) 라 이 질의가 **종목 수만큼 탐색**한다. 국내만 900회다.
--
-- 실측 (2026-09-17, 종목 2,000 × 500일 = 100만 행, 로컬 SQLite):
--   인덱스 없음  0.032초  계획 SEARCH p USING COVERING INDEX idx_prices_stock_date (stock_id=?)
--   인덱스 있음  0.000초  계획 SEARCH p USING INDEX idx_prices_date
-- 즉 종목마다 한 번씩 찾던 것이 한 번으로 끝난다. Turso 무료 한도는 읽은 행 수로 센다.
--
-- 대가는 저장 공간과 아주 약간의 삽입 비용이다. 하루에 넣는 행이 2,700 안팎이라 무시할 수 있다.

CREATE INDEX IF NOT EXISTS idx_prices_date ON prices (date DESC);
