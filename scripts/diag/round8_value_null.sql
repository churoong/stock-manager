-- 8회차 2c: 국내 최근 60일 시세 중 거래대금(value)이 빈 행 수와 그런 종목 수 (docs/factors.md 12.10). db-status.yml sql 입력으로(주석 줄 빼고)
SELECT COUNT(*) AS rows_60d, SUM(p.value IS NULL) AS value_null, COUNT(DISTINCT CASE WHEN p.value IS NULL THEN p.stock_id END) AS stocks_with_null FROM stocks s CROSS JOIN prices p WHERE s.country = 'KR' AND p.stock_id = s.id AND p.date >= date('now', '-90 days')
