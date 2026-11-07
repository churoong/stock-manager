-- 38회차 P1′ (docs/factors.md 12.41, 12장 머리 "켜는 기준" 국면 조항, docs/infra.md 25.2179): 국내 기본 실행의 `buffer_2n` 을
-- 공매도 국면별로 나눠 `composite`(같은 실행) 대비 월 초과수익의 평균·부호·달 수를 낸다. **코드와 같은 커밋에 고정**
-- (그 전략은 아직 한 번도 돌지 않았다 — 결과 보기 전). 37회차 `round37_high52_regime.sql` 과 같은 꼴이되:
--   공통 구간 시작이 없다 — 두 전략이 같은 후보·같은 리밸런스 날이라 곡선 전 구간(월수익이 잡히는 둘째 달부터)을 센다
--   월수익 = 달의 마지막 equity 끼리(`luck.monthly_returns` 와 같음). 그 달은 **월말 날짜가 속한 국면**에 센다(28회차와 같음)
--   국면 경계는 28회차와 같다 [확인필요: 금융위원회 보도자료 원문 대조]
-- 대상 실행(`label`): **판정 실행**(12장 머리 정의 — 그 달 trade_date 3~7일 사이 기본·성공 국내 실행 가운데 가장 먼저 시작한 하나)
--   가운데 ① 'first' = 2026-12 이후 `buffer_2n_judge.verdict` 가 처음 통과·탈락인 것 ② '2028-04' = 2028-04 판정 실행(다시 보기 —
--   그 달 판정이 판정 불가여도 판정 기록이 있으면). 그 달 판정 실행이 판정 불가면 다음 달로(같은 달 뒤 손 실행은 세지 않는다)
-- 읽기: 곡선을 한 번씩만 훑는다(윈도 함수 — 25.1651)
WITH judge AS (
  SELECT b.id, b.started_at, substr(b.trade_date, 1, 7) AS mon, json_extract(b.step_log, '$.group_id') AS group_id,
    json_extract(b.step_log, '$.buffer_2n_judge.verdict') AS verdict,
    ROW_NUMBER() OVER (PARTITION BY substr(b.trade_date, 1, 7) ORDER BY b.started_at ASC, b.id ASC) AS k
  FROM batch_runs b
  WHERE b.job_name = 'backtest' AND b.market = 'KR' AND b.status = 'success' AND b.trade_date >= '2026-12-01'
    AND CAST(substr(b.trade_date, 9, 2) AS INTEGER) BETWEEN 3 AND 7
    AND json_valid(b.step_log) AND json_extract(b.step_log, '$.params.default') = 1
), g AS (
  SELECT * FROM (SELECT 'first' AS label, group_id FROM judge WHERE k = 1 AND verdict IN ('통과', '탈락')
                 ORDER BY started_at ASC LIMIT 1)
  UNION ALL
  SELECT '2028-04', group_id FROM judge WHERE k = 1 AND mon = '2028-04' AND verdict IS NOT NULL
), runs AS (
  SELECT r.id, r.strategy, g.label FROM backtest_runs r JOIN g ON g.group_id = r.group_id
  WHERE r.strategy IN ('buffer_2n', 'composite')
), me AS (
  SELECT run_id, m, equity, d FROM (
    SELECT c.run_id, substr(c.date, 1, 7) AS m, c.equity, c.date AS d,
      ROW_NUMBER() OVER (PARTITION BY c.run_id, substr(c.date, 1, 7) ORDER BY c.date DESC) AS rn
    FROM backtest_curves c WHERE c.run_id IN (SELECT id FROM runs)
  ) WHERE rn = 1
), mr AS (
  SELECT runs.label, runs.strategy AS s, x.m, x.equity / x.prev - 1 AS ret, x.d
  FROM (SELECT run_id, m, d, equity, LAG(equity) OVER (PARTITION BY run_id ORDER BY m) AS prev FROM me) x
  JOIN runs ON runs.id = x.run_id
  WHERE x.prev > 0
), pair AS (
  SELECT c.label, c.s AS cand, c.m, c.ret - b.ret AS ex,
    CASE WHEN c.d < '2023-11-06' THEN '1_부분허용' WHEN c.d < '2025-03-31' THEN '2_전면금지' ELSE '3_전면재개' END AS regime
  FROM mr c JOIN mr b ON b.m = c.m AND b.label = c.label AND b.s = 'composite'
  WHERE c.s = 'buffer_2n'
)
SELECT label, cand, regime, COUNT(*) AS months, ROUND(AVG(ex), 5) AS mean_ex, SUM(ex > 0) AS pos_months
FROM pair GROUP BY label, cand, regime
UNION ALL
SELECT label, cand, '0_전체', COUNT(*), ROUND(AVG(ex), 5), SUM(ex > 0) FROM pair GROUP BY label, cand
ORDER BY 1, 2, 3;
