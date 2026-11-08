-- 동결 해제로 열린 판정 대기 원장 행의 일일(주·월) 실행 기록 집계 + 11-03 판정 실행 소요 (초안 2026-11-08, 읽기만)
-- 근거: docs/handoff.md 머리 다음 건 ⑤ · docs/infra.md 25.2181(동결 해제 — #6·#7·#10·#11·#12·#13·#16·#18 은 일일 기록 집계를
-- 다음 db-status 한 번에) · docs/factors.md 12.32(원장 표 — 실행 기록 키) · docs/infra.md 25.1660·25.2183(실제 소요 [확인필요])
-- 돌리는 길: db-status.yml 의 sql 입력에 이 파일 원문 그대로. scripts/db_status.py 가 머리 주석 줄만 걷고 몸은 SELECT/WITH
-- 한 문장이어야 한다(세미콜론·쓰기 낱말 금지 — 몸에는 주석을 두지 않는다). 그래서 묶음을 여러 문장으로 나누지 않고
-- 한 문장의 sec 칸으로 가른다(round42_judge_1103.sql 과 같은 꼴). 합성 SELECT 항은 한 겹에 다섯 이하(D1 항 수 제한 25.5)
-- 칸 넷: sec·mkt·item·val(json_object 글). 모양만 — 수·날짜·비율. 종목·금액·보유·증권사 이름은 내지 않는다
-- 기간: 원장 기록이 들어간 2026-10-14(21~26회차) 이후만 읽는다(batch_runs 는 job_name·trade_date 또는 started_at 색인으로 묶임).
-- per = before_1103 / from_1103 (판정 실행·동결 해제 날 기준으로 나눔, round42_judge_1103.sql daily 묶음과 같다)
-- section: timing — 2026-11-01~07 trade_date 의 backtest·backtest_core 실행마다 상태·분(started→finished)·step_log.timing·
--          db_rows_read·params.default·trigger. 주의: jobs/backtest.py(월 백테스트 = 11-03 판정 실행)는 timing 키를 쓰지
--          않는다(timing 은 jobs/core_backtest.py 만). 그래서 판정 실행은 has_timing 0 이고 분 칸이 실제 소요다
-- section: l06 — broker_stats(금 주간) step_log.clusters.fwd_gate_drop·touch_gate_drop. 접는 조건: 둘 다 늘 0
-- section: l07 — etf_tilt(월) step_log.single_factor.{us,kr}. 조건: share_top > share_all 인 실행 수
-- section: l10 — signals step_log.mid_newer_quarter {signals,with_quarter,lower}. 조건: lower > with_quarter/2 인 실행 수·누계 몫
-- section: l11 — scores step_log.peer_sample.small_markets 의 종목이 1부(report_items.section='recommend')에 실린 수.
--          접는 조건: 실린 적 0. 둘째 조건(n<10 인데 |z|>2 종목의 1부)은 step_log 만으로 못 낸다 — small_metrics n<10 수만
-- section: l12 — scores step_log.small_base.{시장} oi_top/oi_all·equity_top/equity_all. 조건: 상위 몫 ≥ 전체 몫 × 2 인 실행 수
-- section: l13 — daily_kr/us step_log.same_issuer_pairs 와 그날 2부(report_items.section='buy_signal') weight_pct 합.
--          조건: 발행사 합 > 10% 인 날 수
-- section: l16 — daily_kr/us step_log.impact {n,unknown,over,impact_max_pct}. 조건: over > 0 (충격 > 편도 0.10%) 인 날 수
-- section: l18 — sell_flags step_log.dividend_note {stop_target,with_div}. 조건: with_div 가 쌓였나(누계·걸린 날)
WITH br AS (
  SELECT b.id, b.job_name, b.market, b.trade_date, b.started_at, b.step_log,
    CASE WHEN b.trade_date >= '2026-11-03' THEN 'from_1103' ELSE 'before_1103' END AS per
  FROM batch_runs b
  WHERE b.job_name IN ('broker_stats', 'signals', 'scores', 'daily_kr', 'daily_us', 'sell_flags')
    AND b.trade_date >= '2026-10-14' AND b.status IN ('success', 'partial') AND json_valid(b.step_log)
), et AS (
  SELECT b.id, b.trade_date, b.started_at, b.step_log,
    CASE WHEN b.started_at >= '2026-11-03' THEN 'from_1103' ELSE 'before_1103' END AS per
  FROM batch_runs b
  WHERE b.job_name = 'etf_tilt' AND b.started_at >= '2026-10-14' AND b.status IN ('success', 'partial')
    AND json_valid(b.step_log)
), sc AS (
  SELECT * FROM br WHERE job_name = 'scores'
), dl AS (
  SELECT * FROM br WHERE job_name IN ('daily_kr', 'daily_us')
), rep AS (
  SELECT r.id, r.market, r.trade_date FROM daily_reports r
  WHERE r.market IN ('KR', 'US') AND r.trade_date >= '2026-10-14'
), sm AS (
  SELECT sc.market AS mkt, CAST(s.value AS INTEGER) AS sid, MIN(sc.trade_date) AS first_seen
  FROM sc, json_each(sc.step_log, '$.peer_sample.small_markets') g, json_each(g.value, '$.stocks') s
  GROUP BY 1, 2
), sm_hit AS (
  SELECT rep.market AS mkt, rep.trade_date, i.stock_id
  FROM rep JOIN report_items i ON i.report_id = rep.id AND i.section = 'recommend'
  JOIN sm ON sm.mkt = rep.market AND sm.sid = i.stock_id AND rep.trade_date >= sm.first_seen
), pr AS (
  SELECT DISTINCT dl.market, dl.trade_date, dl.per, p.value AS pk, t.value AS ticker
  FROM dl, json_each(dl.step_log, '$.same_issuer_pairs') p, json_each(p.value) t
), pw AS (
  SELECT pr.market, pr.trade_date, pr.per, pr.pk, COUNT(i.id) AS rows2, SUM(json_extract(i.payload_json, '$.weight_pct')) AS w
  FROM pr LEFT JOIN rep ON rep.market = pr.market AND rep.trade_date = pr.trade_date
  LEFT JOIN report_items i ON i.report_id = rep.id AND i.section = 'buy_signal'
    AND json_extract(i.payload_json, '$.ticker') = pr.ticker
  GROUP BY 1, 2, 3, 4
), part_a AS (
  SELECT 'timing' AS sec, b.market AS mkt, b.job_name || '#' || b.id AS item,
    json_object('trade_date', b.trade_date, 'status', b.status, 'started_at', b.started_at, 'finished_at', b.finished_at,
      'minutes', round((julianday(b.finished_at) - julianday(b.started_at)) * 1440, 1),
      'default', json_extract(b.step_log, '$.params.default'), 'trigger', json_extract(b.step_log, '$.params.trigger'),
      'has_timing', json_type(b.step_log, '$.timing') IS NOT NULL, 'timing', json_extract(b.step_log, '$.timing'),
      'db_rows_read', json_extract(b.step_log, '$.db_rows_read'), 'stocks', json_extract(b.step_log, '$.stocks'),
      'dates', json_extract(b.step_log, '$.dates'), 'progress', json_extract(b.step_log, '$.progress')) AS val
  FROM batch_runs b
  WHERE b.job_name IN ('backtest', 'backtest_core') AND b.trade_date BETWEEN '2026-11-01' AND '2026-11-07'
    AND json_valid(COALESCE(b.step_log, '{}'))
  UNION ALL
  SELECT 'l06', br.market, 'clusters_' || br.per,
    json_object('runs', COUNT(*), 'with_key', SUM(json_type(br.step_log, '$.clusters') IS NOT NULL),
      'fwd_gate_drop_max', MAX(json_extract(br.step_log, '$.clusters.fwd_gate_drop')),
      'touch_gate_drop_max', MAX(json_extract(br.step_log, '$.clusters.touch_gate_drop')),
      'runs_drop_nonzero', SUM(COALESCE(json_extract(br.step_log, '$.clusters.fwd_gate_drop'), 0)
        + COALESCE(json_extract(br.step_log, '$.clusters.touch_gate_drop'), 0) > 0),
      'brokers_max', MAX(json_extract(br.step_log, '$.brokers')),
      'brokers_ready_max', MAX(json_extract(br.step_log, '$.brokers_ready')),
      'first', MIN(br.trade_date), 'last', MAX(br.trade_date))
  FROM br WHERE br.job_name = 'broker_stats' GROUP BY br.market, br.per
  UNION ALL
  SELECT 'l07', s.key, 'single_factor_' || et.per,
    json_object('runs', COUNT(*), 'with_value', SUM(s.type = 'object'),
      'runs_top_gt_all', SUM(json_extract(s.value, '$.share_top') > json_extract(s.value, '$.share_all')),
      'share_top_max', MAX(json_extract(s.value, '$.share_top')), 'share_all_min', MIN(json_extract(s.value, '$.share_all')),
      'scored_max', MAX(json_extract(s.value, '$.scored')), 'top_max', MAX(json_extract(s.value, '$.top')),
      'first', MIN(substr(et.started_at, 1, 10)), 'last', MAX(substr(et.started_at, 1, 10)))
  FROM et, json_each(et.step_log, '$.single_factor') s GROUP BY s.key, et.per
  UNION ALL
  SELECT 'l10', br.market, 'mid_newer_quarter_' || br.per,
    json_object('runs', COUNT(*), 'with_key', SUM(json_type(br.step_log, '$.mid_newer_quarter') IS NOT NULL),
      'errors', SUM(json_type(br.step_log, '$.mid_newer_quarter.error') IS NOT NULL),
      'signals', SUM(json_extract(br.step_log, '$.mid_newer_quarter.signals')),
      'with_quarter', SUM(json_extract(br.step_log, '$.mid_newer_quarter.with_quarter')),
      'lower', SUM(json_extract(br.step_log, '$.mid_newer_quarter.lower')),
      'lower_share', round(1.0 * SUM(json_extract(br.step_log, '$.mid_newer_quarter.lower'))
        / NULLIF(SUM(json_extract(br.step_log, '$.mid_newer_quarter.with_quarter')), 0), 4),
      'runs_over_half', SUM(2 * json_extract(br.step_log, '$.mid_newer_quarter.lower')
        > json_extract(br.step_log, '$.mid_newer_quarter.with_quarter')),
      'first', MIN(br.trade_date), 'last', MAX(br.trade_date))
  FROM br WHERE br.job_name = 'signals' GROUP BY br.market, br.per
  UNION ALL
  SELECT 'l11', sc.market, 'peer_sample_' || sc.per,
    json_object('runs', COUNT(*), 'with_key', SUM(json_type(sc.step_log, '$.peer_sample') IS NOT NULL),
      'runs_small_market', SUM(COALESCE((SELECT COUNT(*) FROM json_each(sc.step_log, '$.peer_sample.small_markets')), 0) > 0),
      'small_groups_max', MAX((SELECT COUNT(*) FROM json_each(sc.step_log, '$.peer_sample.small_markets'))),
      'stocks_listed_max', MAX((SELECT SUM(json_array_length(g.value, '$.stocks'))
        FROM json_each(sc.step_log, '$.peer_sample.small_markets') g)),
      'groups_truncated', MAX((SELECT COUNT(*) FROM json_each(sc.step_log, '$.peer_sample.small_markets') g
        WHERE json_extract(g.value, '$.n') > json_array_length(g.value, '$.stocks'))),
      'small_metrics_total_max', MAX(json_extract(sc.step_log, '$.peer_sample.small_metrics_total')),
      'metrics_n_lt10_max', MAX((SELECT COUNT(*) FROM json_each(sc.step_log, '$.peer_sample.small_metrics') m
        WHERE json_extract(m.value, '$.n') < 10)),
      'part1_items', (SELECT COUNT(*) FROM sm_hit h WHERE h.mkt = sc.market
        AND (h.trade_date >= '2026-11-03') = (sc.per = 'from_1103')),
      'part1_days', (SELECT COUNT(DISTINCT h.trade_date) FROM sm_hit h WHERE h.mkt = sc.market
        AND (h.trade_date >= '2026-11-03') = (sc.per = 'from_1103')),
      'part1_stocks', (SELECT COUNT(DISTINCT h.stock_id) FROM sm_hit h WHERE h.mkt = sc.market
        AND (h.trade_date >= '2026-11-03') = (sc.per = 'from_1103')),
      'first', MIN(sc.trade_date), 'last', MAX(sc.trade_date))
  FROM sc GROUP BY sc.market, sc.per
), part_b AS (
  SELECT 'l12' AS sec, s.key AS mkt, 'small_base_' || sc.per AS item,
    json_object('runs', COUNT(*), 'scores_market', MIN(sc.market),
      'runs_oi_2x', SUM(json_extract(s.value, '$.oi_top') >= 2 * json_extract(s.value, '$.oi_all')
        AND json_extract(s.value, '$.oi_top') > 0),
      'runs_equity_2x', SUM(json_extract(s.value, '$.equity_top') >= 2 * json_extract(s.value, '$.equity_all')
        AND json_extract(s.value, '$.equity_top') > 0),
      'oi_ratio_max', round(MAX(json_extract(s.value, '$.oi_top') / NULLIF(json_extract(s.value, '$.oi_all'), 0)), 3),
      'equity_ratio_max', round(MAX(json_extract(s.value, '$.equity_top')
        / NULLIF(json_extract(s.value, '$.equity_all'), 0)), 3),
      'n_max', MAX(json_extract(s.value, '$.n')), 'top_n_max', MAX(json_extract(s.value, '$.top_n')),
      'first', MIN(sc.trade_date), 'last', MAX(sc.trade_date)) AS val
  FROM sc, json_each(sc.step_log, '$.small_base') s GROUP BY s.key, sc.per
  UNION ALL
  SELECT 'l13', dl.market, 'same_issuer_' || dl.per,
    json_object('runs', COUNT(*), 'days', COUNT(DISTINCT dl.trade_date),
      'runs_with_pairs', SUM(COALESCE(json_array_length(dl.step_log, '$.same_issuer_pairs'), 0) > 0),
      'pairs_total', SUM(COALESCE(json_array_length(dl.step_log, '$.same_issuer_pairs'), 0)),
      'pair_days', (SELECT COUNT(DISTINCT pw.trade_date) FROM pw WHERE pw.market = dl.market AND pw.per = dl.per),
      'pair_days_no_part2', (SELECT COUNT(DISTINCT pw.trade_date) FROM pw WHERE pw.market = dl.market AND pw.per = dl.per
        AND pw.rows2 = 0),
      'days_issuer_over10', (SELECT COUNT(DISTINCT pw.trade_date) FROM pw WHERE pw.market = dl.market AND pw.per = dl.per
        AND pw.w > 10),
      'issuer_pct_max', (SELECT round(MAX(pw.w), 2) FROM pw WHERE pw.market = dl.market AND pw.per = dl.per),
      'first', MIN(dl.trade_date), 'last', MAX(dl.trade_date))
  FROM dl GROUP BY dl.market, dl.per
  UNION ALL
  SELECT 'l16', dl.market, 'impact_' || dl.per,
    json_object('runs', COUNT(*), 'with_key', SUM(json_type(dl.step_log, '$.impact') IS NOT NULL),
      'n', SUM(json_extract(dl.step_log, '$.impact.n')), 'unknown', SUM(json_extract(dl.step_log, '$.impact.unknown')),
      'runs_all_unknown', SUM(json_extract(dl.step_log, '$.impact.n') = 0 AND json_extract(dl.step_log, '$.impact.unknown') > 0),
      'over', SUM(json_extract(dl.step_log, '$.impact.over')),
      'days_over', COUNT(DISTINCT CASE WHEN json_extract(dl.step_log, '$.impact.over') > 0 THEN dl.trade_date END),
      'impact_max_pct', MAX(json_extract(dl.step_log, '$.impact.impact_max_pct')),
      'impact_median_max_pct', MAX(json_extract(dl.step_log, '$.impact.impact_median_pct')),
      'threshold_pct', MAX(json_extract(dl.step_log, '$.impact.threshold_pct')),
      'first', MIN(dl.trade_date), 'last', MAX(dl.trade_date))
  FROM dl GROUP BY dl.market, dl.per
  UNION ALL
  SELECT 'l18', COALESCE(br.market, '-'), 'dividend_note_' || br.per,
    json_object('runs', COUNT(*), 'with_key', SUM(json_type(br.step_log, '$.dividend_note') IS NOT NULL),
      'stop_target', SUM(json_extract(br.step_log, '$.dividend_note.stop_target')),
      'with_div', SUM(json_extract(br.step_log, '$.dividend_note.with_div')),
      'days_with_div', COUNT(DISTINCT CASE WHEN json_extract(br.step_log, '$.dividend_note.with_div') > 0 THEN br.trade_date END),
      'first', MIN(br.trade_date), 'last', MAX(br.trade_date))
  FROM br WHERE br.job_name = 'sell_flags' GROUP BY br.market, br.per
)
SELECT sec, mkt, item, val FROM part_a
UNION ALL
SELECT sec, mkt, item, val FROM part_b
ORDER BY 1, 2, 3
