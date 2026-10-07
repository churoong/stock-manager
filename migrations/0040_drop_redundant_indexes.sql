-- 겹치는 인덱스를 모두 지운다 (docs/infra.md 25.25). 0039 의 `prices` 와 같은 이유다.
--
-- **D1 은 쓰기를 셀 때 인덱스까지 센다.** 한 행을 넣으면 `1 + 인덱스 수` 만큼 하루 한도(10만 행)를
-- 태운다. 0039 에서 `prices` 하나를 지워 배수가 4 → 3 이 됐고, 그때 **다른 표도 훑어보니 같은
-- 모양이 아홉 개 더 있었다.** 전부 `UNIQUE (…)` 가 만드는 자동 인덱스의 **접두사**다.
--
-- 왜 접두사면 지워도 되나. 인덱스 `(a, b)` 로 답할 수 있는 질의는 `(a, b, c)` 로도 똑같이 답한다 —
-- 앞에서부터 같은 제약을 쓰기 때문이다. 덮개(covering) 여부도 달라지지 않는다. `(a, b, c)` 는
-- `(a, b)` 가 가진 열을 모두 갖고 있다. 대가는 인덱스가 조금 더 크다는 것뿐이고, 얻는 것은
-- **쓰기가 표마다 한 번씩 줄어드는 것**이다.
--
-- 실측 (2026-09-20, 로컬 SQLite 에 같은 스키마를 세우고 대표 질의로 `EXPLAIN QUERY PLAN` 대조).
-- 열 개 모두 **실행계획이 인덱스 이름만 바뀌고 접근 방식이 같았다.** 전체 훑기(SCAN)로 떨어지거나
-- 정렬용 임시 B-TREE 가 새로 생긴 것은 하나도 없다:
--
--   지운 것                        대신 쓰는 것                                    접두사 관계
--   idx_metrics_stock            sqlite_autoindex_performance_metrics_1        (stock_id, as_of_date, window) ⊂ (…, calc_version)
--   idx_factors_stock            sqlite_autoindex_factors_1                    (stock_id, as_of_date, factor) ⊂ (…, calc_version)
--   idx_scores_stock             sqlite_autoindex_scores_1                     (stock_id, as_of_date) ⊂ (…, calc_version)
--   idx_signals_stock            sqlite_autoindex_signals_1                    (stock_id, as_of_date) ⊂ (…, horizon, calc_version)
--   idx_valuation_bands_stock    sqlite_autoindex_valuation_bands_1            (stock_id, as_of_date) ⊂ (…, metric, calc_version)
--   idx_financials_stock         sqlite_autoindex_financials_1                 (stock_id, fiscal_year, report_code) ⊂ (…, consolidated)
--   idx_stock_dividends_stock    sqlite_autoindex_stock_dividends_1            (stock_id, fiscal_year) ⊂ (…, report_year)
--   idx_signal_checks_stock      sqlite_autoindex_signal_checks_1              (stock_id, as_of_date) ⊂ (…, horizon)
--   idx_market_calendar_date     sqlite_autoindex_market_calendar_1            (exchange, date) = 같은 열
--   idx_etf_profiles_etf         sqlite_autoindex_etf_profiles_1               (etf_id, as_of_date) = 같은 열
--
-- 효과 (따라잡기가 쓰는 표들의 배수):
--   performance_metrics 4 → 3 · factors 4 → 3 · scores 4 → 3 · signals 4 → 3
--   valuation_bands 3 → 2 · financials 4 → 3
-- 점수·신호 단계가 쓰는 행이 약 8,000 이므로 3.2만 여유 안에서 도는 데 여유가 생긴다(infra 25.8).
--
-- **날짜 쪽 인덱스(`idx_*_date`, `idx_metrics_window`)는 남긴다.** 그것들은 접두사가 아니라
-- 열 순서가 달라(날짜가 앞) 종목을 가로지르는 질의가 쓴다.
--
-- 앞으로 같은 실수를 막는 장치: `tests/test_index_redundancy.py` 가 **모든 표**를 훑어
-- 접두사 인덱스가 새로 생기면 실패한다.

DROP INDEX IF EXISTS idx_metrics_stock;
DROP INDEX IF EXISTS idx_factors_stock;
DROP INDEX IF EXISTS idx_scores_stock;
DROP INDEX IF EXISTS idx_signals_stock;
DROP INDEX IF EXISTS idx_valuation_bands_stock;
DROP INDEX IF EXISTS idx_financials_stock;
DROP INDEX IF EXISTS idx_stock_dividends_stock;
DROP INDEX IF EXISTS idx_signal_checks_stock;
DROP INDEX IF EXISTS idx_market_calendar_date;
DROP INDEX IF EXISTS idx_etf_profiles_etf;
