-- 국내 ETF 괴리율 평균을 낸 **NAV 가 있는 날 수** (docs/infra.md 25.841, 2026-10-01 사용자 결정).
--
-- 핵심 판정은 이 값을 입력으로 받아 근거표에 "N일 평균 |종가−NAV|/NAV" 를 바르게 적는다(25.714). 그런데 프로필에 저장하지 않아
-- 위성 판정(`batch/jobs/etf_satellite.py`)은 저장된 프로필을 읽을 때 이 값이 없어 거래대금이 있는 날 수(`days_observed`)로 갈음했다 —
-- NAV 가 10일뿐인 ETF 가 위성 근거표에 "20일 평균" 으로 적혔다. 열을 더할 뿐이라 옛 행은 NULL(그때는 예전처럼 days_observed 로 적는다).
ALTER TABLE etf_profiles ADD COLUMN premium_days INTEGER;
