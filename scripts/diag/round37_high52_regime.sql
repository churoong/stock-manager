-- 37회차 A (docs/factors.md 12.40, 12장 머리 "켜는 기준" 국면 조항, docs/infra.md 25.1648): 국내 기본 실행의 `high52` 를
-- 공매도 국면별로 나눠 `momentum`(같은 실행) 대비 월 초과수익의 평균·부호·달 수를 낸다. **11-03 예약 실행 전에 고정**(결과 보기 전,
-- 28회차 고정본 `round28_short_regime.sql` 은 건드리지 않고 따로 둔다 — 검증 A). 저장된 `backtest_curves` 와 그 실행 기록의
-- 공통 구간 시작(`batch_runs.step_log.high52_judge.start` — 처음 상위 N 을 다 채운 리밸런스)만 읽는다.
--   월수익 = 달의 마지막 equity 끼리(`luck.monthly_returns` 와 같음). 그 달은 **월말 날짜가 속한 국면**에 센다(28회차와 같음)
--   **공통 구간 시작 달 다음 달부터만** 센다 — `high52_judge` 의 월 초과와 같은 달들(그 앞은 252일이 안 쌓여 현금으로 쉰 달,
--   부분 허용 국면 평균을 비튼다 — 37회차 교차검증 [필수])
--   국면 경계는 28회차와 같다 [확인필요: 금융위원회 보도자료 원문 대조]
-- 대상 실행(`label`): **판정 실행**(12장 머리 정의 — 그 달 trade_date 3~7일 사이 기본·성공 국내 실행 가운데 가장 먼저 시작한 하나)
--   가운데 ① 'first' = 2026-11 이후 `high52_judge.verdict` 가 처음 통과·탈락인 것 ② '2028-04' = 2028-04 판정 실행(다시 보기 — 그 달
--   판정이 판정 불가여도 공통 구간 시작이 있으면, 25.1655).
--   그 달 판정 실행이 판정 불가면 다음 달로(손으로 돌린 같은 달 뒤 실행은 세지 않는다 — 37회차 교차검증 [권고], 25.1651)
-- 읽기: 곡선을 한 번씩만 훑는다(윈도 함수 — 상관 하위 질의는 곡선 길이의 네제곱으로 읽어 월 한도를 넘었다, 25.1651)
WITH judge AS (
  SELECT b.id, b.started_at, substr(b.trade_date, 1, 7) AS mon, json_extract(b.step_log, '$.group_id') AS group_id,
    json_extract(b.step_log, '$.high52_judge.start') AS start, json_extract(b.step_log, '$.high52_judge.verdict') AS verdict,
    ROW_NUMBER() OVER (PARTITION BY substr(b.trade_date, 1, 7) ORDER BY b.started_at ASC, b.id ASC) AS k
  FROM batch_runs b
  WHERE b.job_name = 'backtest' AND b.market = 'KR' AND b.status = 'success' AND b.trade_date >= '2026-11-01'
    AND CAST(substr(b.trade_date, 9, 2) AS INTEGER) BETWEEN 3 AND 7
    AND json_valid(b.step_log) AND json_extract(b.step_log, '$.params.default') = 1
), g AS (
  SELECT * FROM (SELECT 'first' AS label, group_id, start FROM judge WHERE k = 1 AND verdict IN ('통과', '탈락')
                 ORDER BY started_at ASC LIMIT 1)
  UNION ALL
  SELECT '2028-04', group_id, start FROM judge WHERE k = 1 AND mon = '2028-04' AND start IS NOT NULL
), runs AS (
  SELECT r.id, r.strategy, g.label, g.start FROM backtest_runs r JOIN g ON g.group_id = r.group_id
  WHERE r.strategy IN ('high52', 'momentum')
), me AS (
  SELECT run_id, m, equity, d FROM (
    SELECT c.run_id, substr(c.date, 1, 7) AS m, c.equity, c.date AS d,
      ROW_NUMBER() OVER (PARTITION BY c.run_id, substr(c.date, 1, 7) ORDER BY c.date DESC) AS rn
    FROM backtest_curves c WHERE c.run_id IN (SELECT id FROM runs)
  ) WHERE rn = 1
), mr AS (
  SELECT runs.label, runs.start, runs.strategy AS s, x.m, x.equity / x.prev - 1 AS ret, x.d
  FROM (SELECT run_id, m, d, equity, LAG(equity) OVER (PARTITION BY run_id ORDER BY m) AS prev FROM me) x
  JOIN runs ON runs.id = x.run_id
  WHERE x.prev > 0
), pair AS (
  SELECT c.label, c.s AS cand, c.m, c.ret - b.ret AS ex,
    CASE WHEN c.d < '2023-11-06' THEN '1_부분허용' WHEN c.d < '2025-03-31' THEN '2_전면금지' ELSE '3_전면재개' END AS regime
  FROM mr c JOIN mr b ON b.m = c.m AND b.label = c.label AND b.s = 'momentum'
  WHERE c.s = 'high52' AND c.m > substr(c.start, 1, 7)
)
SELECT label, cand, regime, COUNT(*) AS months, ROUND(AVG(ex), 5) AS mean_ex, SUM(ex > 0) AS pos_months
FROM pair GROUP BY label, cand, regime
UNION ALL
SELECT label, cand, '0_전체', COUNT(*), ROUND(AVG(ex), 5), SUM(ex > 0) FROM pair GROUP BY label, cand
ORDER BY 1, 2, 3;
