-- 국내 ETF 판정에 쓰는 값 (docs/etf.md 9장).
--
-- 국내는 한국거래소 ETF 일별매매정보에서 받는다. 미국 프로필에 없던 값이 셋 생긴다.
-- 근거표의 모든 행은 DB 의 실제 열이어야 하므로(etf.md 3장) 열로 둔다.
--
-- 미국 행은 셋 다 NULL 이다.

ALTER TABLE etf_profiles ADD COLUMN premium_abs_avg REAL;   -- 괴리율 |종가−NAV|/NAV 의 평균
ALTER TABLE etf_profiles ADD COLUMN days_observed INTEGER;  -- 평균을 낸 거래일 수
ALTER TABLE etf_profiles ADD COLUMN listed_3y_ago INTEGER;  -- 3년 전 같은 무렵 응답에 있었으면 1
