-- 13회차 진단 (docs/factors.md 12.15, docs/infra.md 25.954): 유니버스 시총의 가격 날짜(stocks.market_cap_date)와
-- 점수 기준일 사이에 가격이 얼마나 움직였나. 관문(결과 보기 전에 고정): |배수−1| > 1% 인 종목이 유니버스의 1% 미만이면
-- 구현하지 않고 기록만 한다. 2026-10-05 03:40 UTC 국내 실측: 874종목, 시총 날짜 전부 2026-09-16(스냅샷 10-02 — KRX 빈 응답으로
-- 지난 시총 복사), >1% 781 · >3% 614 · >5% 495 · >10% 303 → 관문 통과. 배수는 진단용으로 수정종가 비율(운영은 국내 등락률 곱).
WITH c AS (
  SELECT MAX(sc.as_of_date) d FROM scores sc JOIN stocks s ON s.id = sc.stock_id WHERE s.country = 'KR'
), m AS (
  SELECT u.stock_id, u.market_cap, s.market_cap_date md, u.snapshot_date sd
  FROM universe_members u JOIN stocks s ON s.id = u.stock_id
  WHERE s.country = 'KR' AND u.included = 1
    AND u.snapshot_date = (SELECT MAX(u2.snapshot_date) FROM universe_members u2 JOIN stocks s2 ON s2.id = u2.stock_id
                           WHERE s2.country = 'KR')
), r AS (
  SELECT m.stock_id, m.md, m.sd,
    (SELECT COALESCE(adj_close, close) FROM prices WHERE stock_id = m.stock_id AND date = (SELECT d FROM c))
    / (SELECT COALESCE(adj_close, close) FROM prices WHERE stock_id = m.stock_id AND date = m.md) AS ratio
  FROM m
)
SELECT (SELECT d FROM c) AS score_date, MIN(sd) AS snapshot, MIN(md) AS cap_date_min, MAX(md) AS cap_date_max,
  COUNT(*) AS n, SUM(ratio IS NULL) AS unknown,
  SUM(abs(ratio - 1) > 0.01) AS gt1, SUM(abs(ratio - 1) > 0.03) AS gt3, SUM(abs(ratio - 1) > 0.05) AS gt5,
  SUM(abs(ratio - 1) > 0.10) AS gt10
FROM r;
