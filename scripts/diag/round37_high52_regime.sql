-- 37회차 A (docs/factors.md 12.40, 12장 머리 "켜는 기준" 국면 조항, docs/infra.md 25.1648): 국내 기본 실행의 `high52` 를
-- 공매도 국면별로 나눠 `momentum`(같은 실행) 대비 월 초과수익의 평균·부호·달 수를 낸다. **11-03 예약 실행 전에 고정**(결과 보기 전,
-- 28회차 고정본 `round28_short_regime.sql` 은 건드리지 않고 따로 둔다 — 검증 A). 저장된 `backtest_curves` 와 그 실행 기록의
-- 공통 구간 시작(`batch_runs.step_log.high52_judge.start` — 처음 상위 N 을 다 채운 리밸런스)만 읽는다.
--   월수익 = 달의 마지막 equity 끼리(`luck.monthly_returns` 와 같음). 그 달은 **월말 날짜가 속한 국면**에 센다(28회차와 같음)
--   **공통 구간 시작 달 다음 달부터만** 센다 — `high52_judge` 의 월 초과와 같은 달들(그 앞은 252일이 안 쌓여 현금으로 쉰 달,
--   부분 허용 국면 평균을 비튼다 — 37회차 교차검증 [필수])
--   국면 경계는 28회차와 같다 [확인필요: 금융위원회 보도자료 원문 대조]
-- 대상 실행: 2026-11-03 이후 국내 실행 가운데 `high52` 판정(`high52_judge.verdict`)이 처음 통과·탈락으로 난 실행 — 판정에 쓴 그 달
-- 첫 기본 실행과 같은지 실행 기록(params.default)으로 대조한다. 다르면 이 조건을 고치지 말고 그 group_id 를 적어 다시 돈다
WITH g AS (
  SELECT json_extract(b.step_log, '$.group_id') AS group_id, json_extract(b.step_log, '$.high52_judge.start') AS start
  FROM batch_runs b
  WHERE b.job_name = 'backtest' AND b.market = 'KR' AND b.status = 'success' AND b.started_at >= '2026-11-03'
    AND json_valid(b.step_log) AND json_extract(b.step_log, '$.params.default') = 1
    AND json_extract(b.step_log, '$.high52_judge.verdict') IN ('통과', '탈락')
  ORDER BY b.started_at ASC, b.id ASC LIMIT 1
), runs AS (
  SELECT r.id, r.strategy FROM backtest_runs r JOIN g ON g.group_id = r.group_id
  WHERE r.strategy IN ('high52', 'momentum')
), me AS (
  SELECT c.run_id, substr(c.date, 1, 7) AS m, c.equity
  FROM backtest_curves c JOIN runs ON runs.id = c.run_id
  WHERE c.date = (SELECT MAX(c2.date) FROM backtest_curves c2
                  WHERE c2.run_id = c.run_id AND substr(c2.date, 1, 7) = substr(c.date, 1, 7))
), mr AS (
  SELECT runs.strategy AS s, a.m, a.equity / b.equity - 1 AS ret,
    (SELECT MAX(c3.date) FROM backtest_curves c3 WHERE c3.run_id = a.run_id AND substr(c3.date, 1, 7) = a.m) AS d
  FROM me a JOIN me b ON b.run_id = a.run_id
    AND b.m = (SELECT MAX(x.m) FROM me x WHERE x.run_id = a.run_id AND x.m < a.m)
  JOIN runs ON runs.id = a.run_id
  WHERE b.equity > 0
), pair AS (
  SELECT c.s AS cand, c.m, c.ret - b.ret AS ex,
    CASE WHEN c.d < '2023-11-06' THEN '1_부분허용' WHEN c.d < '2025-03-31' THEN '2_전면금지' ELSE '3_전면재개' END AS regime
  FROM mr c JOIN mr b ON b.m = c.m AND b.s = 'momentum'
  WHERE c.s = 'high52' AND c.m > (SELECT substr(start, 1, 7) FROM g)
)
SELECT cand, regime, COUNT(*) AS months, ROUND(AVG(ex), 5) AS mean_ex, SUM(ex > 0) AS pos_months
FROM pair GROUP BY cand, regime
UNION ALL
SELECT cand, '0_전체', COUNT(*), ROUND(AVG(ex), 5), SUM(ex > 0) FROM pair GROUP BY cand
ORDER BY 1, 2;
