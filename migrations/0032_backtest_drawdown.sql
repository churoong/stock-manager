-- 자본곡선의 낙폭 (docs/backtest.md 8.4). 화면이 낙폭 차트를 그리려면 필요한데, 웹은 계산하지
-- 않는다는 규칙이라 배치가 저장할 때 함께 낸다(services/backtest.drawdown_curve).
-- 이전 실행의 행은 NULL 로 남고 화면은 "다시 돌리면 나온다" 고 알린다.
ALTER TABLE backtest_curves ADD COLUMN drawdown REAL;
