-- 42회차 판정 단계 12월 사본(docs/infra.md 25.2185): `round42_judge_1103.sql` 과 **달만 바꾼** 질의다(몸의 '2026-11' 한 곳만
-- '2026-12'). 2026-12-03 판정 실행 **전에** 커밋한다(값 보기 전). 쓰는 곳: 11-03 에서 '모름'(끝 달 경계)이던 미국 ic_weighted 의
-- 첫 판정(12장 머리 — 처음 통과·탈락이 난 달의 판정 실행 하나), 그리고 11-03 에 판정 불가였던 짝(업종 채움)의 다시 보기. 셈·칸·판정
-- 순서는 11월 질의 머리 그대로다(그 문서가 정의처). daily 묶음은 11월과 같은 칸을 다시 내지만 쓰지 않는다(바꾸지 않으려고 그대로 둠)
-- 이미 첫 판정이 난 줄(국내 ic_weighted·두 나라 shrink_missing 탈락, 25.2181)은 이 질의 결과로 다시 정하지 않는다 — 기록만
WITH judge AS (
  SELECT b.id, b.market, b.trade_date, b.started_at, b.step_log,
    json_extract(b.step_log, '$.group_id') AS group_id,
    ROW_NUMBER() OVER (PARTITION BY b.market ORDER BY b.started_at ASC, b.id ASC) AS k
  FROM batch_runs b
  WHERE b.job_name = 'backtest' AND b.status = 'success' AND substr(b.trade_date, 1, 7) = '2026-12'
    AND CAST(substr(b.trade_date, 9, 2) AS INTEGER) BETWEEN 3 AND 7
    AND json_valid(b.step_log) AND json_extract(b.step_log, '$.params.default') = 1
), j AS (
  SELECT * FROM judge WHERE k = 1
), runs AS (
  SELECT r.id, r.market, r.strategy, r.metrics_json, r.warnings_json, r.turnover_avg, r.rebalances, r.calc_version,
    r.scoring_calc_version, substr(j.trade_date, 1, 7) AS run_month
  FROM backtest_runs r JOIN j ON j.group_id = r.group_id AND j.market = r.market
  WHERE r.strategy IN ('composite', 'ic_weighted', 'shrink_missing', 'momentum', 'momentum_sector')
), me AS (
  SELECT run_id, m, equity FROM (
    SELECT c.run_id, substr(c.date, 1, 7) AS m, c.equity,
      ROW_NUMBER() OVER (PARTITION BY c.run_id, substr(c.date, 1, 7) ORDER BY c.date DESC) AS rn
    FROM backtest_curves c WHERE c.run_id IN (SELECT id FROM runs)
  ) WHERE rn = 1
), mr AS (
  SELECT runs.market AS mkt, runs.strategy AS s, runs.run_month, x.m, x.equity / x.prev - 1 AS ret
  FROM (SELECT run_id, m, equity, LAG(equity) OVER (PARTITION BY run_id ORDER BY m) AS prev FROM me) x
  JOIN runs ON runs.id = x.run_id
  WHERE x.prev > 0
), pairs AS (
  SELECT 'ic_weighted' AS cand, 'composite' AS base
  UNION ALL SELECT 'shrink_missing', 'composite'
  UNION ALL SELECT 'momentum_sector', 'momentum'
), ex AS (
  SELECT c.mkt, p.cand, c.m, c.run_month, c.ret - b.ret AS x
  FROM pairs p JOIN mr c ON c.s = p.cand JOIN mr b ON b.s = p.base AND b.mkt = c.mkt AND b.m = c.m
), ser AS (
  SELECT mkt, cand, 'all' AS v, m, x FROM ex
  UNION ALL
  SELECT mkt, cand, 'excl', m, x FROM ex WHERE m <> run_month
), idx AS (
  SELECT mkt, cand, v, x, ROW_NUMBER() OVER (PARTITION BY mkt, cand, v ORDER BY m) AS i,
    COUNT(*) OVER (PARTITION BY mkt, cand, v) AS n
  FROM ser
), half AS (
  SELECT mkt, cand, v, MAX(n) AS n, AVG(CASE WHEN i <= n / 2 THEN x END) AS h1,
    AVG(CASE WHEN i > n / 2 THEN x END) AS h2
  FROM idx GROUP BY mkt, cand, v
), side AS (
  SELECT p.cand, p.base, c.market AS mkt, c.rebalances AS reb,
    json_extract(c.metrics_json, '$.cagr') AS cc, json_extract(b.metrics_json, '$.cagr') AS cb,
    json_extract(c.metrics_json, '$.sharpe') AS sc, json_extract(b.metrics_json, '$.sharpe') AS sb,
    json_extract(c.metrics_json, '$.mdd') AS dc, json_extract(b.metrics_json, '$.mdd') AS db,
    (p.cand = 'momentum_sector' AND c.warnings_json LIKE '%업종 모멘텀: 업종 평균이 잡힌 종목이%') AS low_cov
  FROM pairs p JOIN runs c ON c.strategy = p.cand JOIN runs b ON b.strategy = p.base AND b.market = c.market
), judged AS (
  SELECT s.*, a.n AS n_all, a.h1 AS h1_all, a.h2 AS h2_all, e.n AS n_excl, e.h1 AS h1_excl, e.h2 AS h2_excl,
    (s.cc > s.cb AND s.sc > s.sb AND s.dc >= s.db - 0.02) AS core,
    (a.h1 > 0 AND a.h2 > 0) AS ok_all, (e.h1 > 0 AND e.h2 > 0) AS ok_excl
  FROM side s
  LEFT JOIN half a ON a.mkt = s.mkt AND a.cand = s.cand AND a.v = 'all'
  LEFT JOIN half e ON e.mkt = s.mkt AND e.cand = s.cand AND e.v = 'excl'
), daily AS (
  SELECT b.job_name, b.market, b.trade_date, b.step_log,
    CASE WHEN b.trade_date >= '2026-11-03' THEN 'from_1103' ELSE 'before_1103' END AS per
  FROM batch_runs b WHERE b.job_name IN ('daily_kr', 'daily_us') AND json_valid(b.step_log)
)
SELECT 'run' AS sec, j.market AS mkt, 'judge_run' AS item,
  json_object('group_id', j.group_id, 'trade_date', j.trade_date, 'started_at', j.started_at,
    'trigger', json_extract(j.step_log, '$.params.trigger'), 'sha', json_extract(j.step_log, '$.params.sha')) AS val
FROM j
UNION ALL
SELECT 'strat', r.market, r.strategy,
  json_object('cagr', json_extract(r.metrics_json, '$.cagr'), 'sharpe', json_extract(r.metrics_json, '$.sharpe'),
    'mdd', json_extract(r.metrics_json, '$.mdd'), 'turnover', r.turnover_avg, 'rebalances', r.rebalances,
    'calc', r.calc_version, 'scoring_calc', r.scoring_calc_version)
FROM runs r
UNION ALL
SELECT 'pair', mkt, cand,
  json_object('base', base, 'rebalances', reb, 'cagr', json_array(cc, cb), 'sharpe', json_array(sc, sb),
    'mdd', json_array(dc, db), 'all', json_array(n_all, h1_all, h2_all), 'excl', json_array(n_excl, h1_excl, h2_excl),
    'low_coverage', low_cov,
    'verdict', CASE
      WHEN cc IS NULL OR cb IS NULL OR sc IS NULL OR sb IS NULL OR dc IS NULL OR db IS NULL
        OR h1_all IS NULL OR h2_all IS NULL THEN '판정 불가'
      WHEN reb < 36 OR low_cov THEN '판정 불가'
      WHEN NOT core THEN '탈락'
      WHEN ok_all AND COALESCE(ok_excl, ok_all) THEN '통과'
      WHEN ok_all <> COALESCE(ok_excl, ok_all) THEN '모름'
      ELSE '탈락' END)
FROM judged
UNION ALL
SELECT 'ic', j.market, e.key,
  json_object('verdict', json_extract(e.value, '$.verdict'), 't', json_extract(e.value, '$.t'),
    'months', json_extract(e.value, '$.months'), 'coverage', json_extract(e.value, '$.coverage'),
    'first_half', json_extract(e.value, '$.first_half'), 'second_half', json_extract(e.value, '$.second_half'),
    'bh_pass', json_extract(e.value, '$.fdr.bh_pass'))
FROM j, json_each(j.step_log, '$.factor_ic') e
UNION ALL
SELECT 'ab', j.market, v.key || '.' || m.key,
  json_object('verdict', json_extract(m.value, '$.verdict'), 'rule_met', json_extract(m.value, '$.rule_met'))
FROM j, json_each(j.step_log, '$.factor_ab.variants') v, json_each(v.value, '$.by_market') m
UNION ALL
SELECT 'h52', j.market, 'high52_judge', json_extract(j.step_log, '$.high52_judge') FROM j
UNION ALL
SELECT 'h52', j.market, 'high52_overlap', json_extract(j.step_log, '$.high52_overlap') FROM j
UNION ALL
SELECT 'daily', d.market, 'corr_async_' || d.per,
  json_object('runs', COUNT(*), 'crossed', SUM(json_extract(d.step_log, '$.correlation.corr_async.crossed')),
    'max_crossed', MAX(json_extract(d.step_log, '$.correlation.corr_async.crossed')),
    'pairs', SUM(json_extract(d.step_log, '$.correlation.corr_async.pairs')),
    'first', MIN(d.trade_date), 'last', MAX(d.trade_date))
FROM daily d WHERE d.job_name IN ('daily_kr', 'daily_us') AND json_extract(d.step_log, '$.correlation.corr_async') IS NOT NULL
GROUP BY d.market, d.per
UNION ALL
SELECT 'daily', d.market, 'fx_vol_' || d.per,
  json_object('runs', COUNT(*), 'lag1_over1', SUM(json_extract(d.step_log, '$.fx_vol.lag1.kappa_over1')),
    'lag1_n', SUM(json_extract(d.step_log, '$.fx_vol.lag1.n')),
    'lag0_over1', SUM(json_extract(d.step_log, '$.fx_vol.lag0.kappa_over1')),
    'first', MIN(d.trade_date), 'last', MAX(d.trade_date))
FROM daily d WHERE d.job_name = 'daily_us' AND json_extract(d.step_log, '$.fx_vol') IS NOT NULL
GROUP BY d.market, d.per
ORDER BY 1, 2, 3;
