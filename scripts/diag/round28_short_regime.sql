-- 28회차 P5 (docs/factors.md 12.30·12장 "켜는 기준" 국면 조항, docs/infra.md 25.1418): 국내 기본 실행의 후보 전략을 공매도 국면별로
-- 나눠 비교 기준 대비 월 초과수익의 평균·부호·달 수를 낸다. **11-03 예약 실행 전에 고정**(결과 보기 전). 계산 경로는 건드리지 않고
-- 저장된 `backtest_curves` 만 읽는다. 실행: db-status.yml 의 sql 입력으로 한 번(국내 첫 기본 실행 group_id 를 아래 g 에 넣는다).
--   2026-10-22 정의는 그대로 두고 계산 꼴만 윈도 함수로 바꿨다(읽기 — 37회차 교차검증 [필수], 25.1651)
--   후보 → 비교 기준(같은 실행 group_id): momentum_sector → momentum, ic_weighted → composite, shrink_missing → composite
--   월수익 = 달의 마지막 equity 끼리(`luck.monthly_returns` 와 같음). 그 달은 **월말 날짜가 속한 국면**에 센다(경계가 걸친 달 포함)
--   국면 [확인필요: 금융위원회 보도자료 원문 대조]: 부분 허용 2021-05-03~2023-11-05(코스피200·코스닥150 만) /
--   전면 금지 2023-11-06~2025-03-30 / 전면 재개 2025-03-31~
-- 대상 실행: 2026-11-03 이후 **첫** 국내 실행 가운데 후보 전략이 있고 상위 20(기본값)인 것 — 판정에 쓴 그 달 첫 기본 실행과
-- 같은지 실행 기록(batch_runs.step_log.params.default)으로 대조한다. 다르면 이 조건을 고치지 말고 그 group_id 를 적어 다시 돈다
WITH g AS (
  SELECT r.group_id FROM backtest_runs r
  WHERE r.market = 'KR' AND r.strategy = 'momentum_sector' AND r.top_n = 20 AND r.created_at >= '2026-11-03'
  ORDER BY r.created_at ASC, r.id ASC LIMIT 1
), runs AS (
  SELECT r.id, r.strategy FROM backtest_runs r JOIN g ON g.group_id = r.group_id
  WHERE r.strategy IN ('momentum_sector', 'ic_weighted', 'shrink_missing', 'momentum', 'composite')
), me AS (
  -- 달마다 마지막 날 하나(윈도 함수 — 예전 상관 하위 질의는 곡선 길이의 네제곱으로 읽었다, 25.1651. 결과는 같다)
  SELECT run_id, m, equity, d FROM (
    SELECT c.run_id, substr(c.date, 1, 7) AS m, c.equity, c.date AS d,
      ROW_NUMBER() OVER (PARTITION BY c.run_id, substr(c.date, 1, 7) ORDER BY c.date DESC) AS rn
    FROM backtest_curves c JOIN runs ON runs.id = c.run_id
  ) WHERE rn = 1
), mr AS (
  SELECT runs.strategy AS s, x.m, x.equity / x.prev - 1 AS ret, x.d
  FROM (SELECT run_id, m, d, equity, LAG(equity) OVER (PARTITION BY run_id ORDER BY m) AS prev FROM me) x
  JOIN runs ON runs.id = x.run_id
  WHERE x.prev > 0
), pair AS (
  SELECT c.s AS cand, c.m, c.ret - b.ret AS ex,
    CASE WHEN c.d < '2023-11-06' THEN '1_부분허용' WHEN c.d < '2025-03-31' THEN '2_전면금지' ELSE '3_전면재개' END AS regime
  FROM mr c JOIN mr b ON b.m = c.m
   AND b.s = CASE c.s WHEN 'momentum_sector' THEN 'momentum' ELSE 'composite' END
  WHERE c.s IN ('momentum_sector', 'ic_weighted', 'shrink_missing')
)
SELECT cand, regime, COUNT(*) AS months, ROUND(AVG(ex), 5) AS mean_ex, SUM(ex > 0) AS pos_months
FROM pair GROUP BY cand, regime
UNION ALL
SELECT cand, '0_전체', COUNT(*), ROUND(AVG(ex), 5), SUM(ex > 0) FROM pair GROUP BY cand
ORDER BY 1, 2;
