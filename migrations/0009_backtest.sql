-- 백테스트와 스트레스 테스트 결과.
--
-- 방법은 docs/backtest.md, docs/stress.md 가 단일 정의처다.
--
-- 경고를 결과와 같은 행에 저장한다. "생존편향: 상장폐지 종목 미포함" 같은
-- 경고가 결과에서 떨어지면 그 숫자는 보이는 것보다 좋은 숫자다.
-- 화면은 경고를 수익률보다 먼저 보여준다.
--
-- 같은 파라미터로 다시 돌리면 같은 결과가 나와야 한다(재현성). 그래서
-- 파라미터를 전부 저장하고, 계산식이 바뀌면 calc_version 을 올린다.

CREATE TABLE IF NOT EXISTS backtest_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market          TEXT NOT NULL,           -- KR US
    strategy        TEXT NOT NULL,           -- composite value quality growth momentum risk benchmark
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    top_n           INTEGER NOT NULL,
    rebalance       TEXT NOT NULL,           -- monthly
    costs_json      TEXT NOT NULL,           -- 쓴 비용. 기본값에 [확인필요] 가 붙어 있어 그대로 표시한다
    rebalances      INTEGER NOT NULL,        -- 리밸런스 횟수
    final_equity    REAL NOT NULL,           -- 시작 1.0 기준
    turnover_avg    REAL,
    excess_cagr     REAL,                    -- 벤치마크 대비
    win_rate        REAL,                    -- 벤치마크를 이긴 달의 비율
    metrics_json    TEXT NOT NULL,           -- services/metrics.compute 결과
    warnings_json   TEXT NOT NULL,           -- 반드시 함께 보여준다
    group_id        TEXT NOT NULL,           -- 같은 실행에서 나온 전략들을 묶는다
    calc_version    INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_group
    ON backtest_runs (group_id, strategy);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_recent
    ON backtest_runs (market, created_at DESC);

-- 일별 자본곡선. 벤치마크도 전략 이름으로 같은 표에 둔다.
CREATE TABLE IF NOT EXISTS backtest_curves (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   INTEGER NOT NULL REFERENCES backtest_runs (id),
    date     TEXT NOT NULL,
    equity   REAL NOT NULL,
    UNIQUE (run_id, date)
);

CREATE TABLE IF NOT EXISTS stress_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market          TEXT NOT NULL,
    as_of_date      TEXT NOT NULL,           -- 바스켓을 가져온 신호 기준일
    weighting       TEXT NOT NULL,           -- suggested equal
    cash_weight     REAL NOT NULL,
    basket_json     TEXT NOT NULL,           -- {stock_id: 비중}. 무엇을 봤는지
    excluded_json   TEXT NOT NULL,           -- 시작일 가격이 없어 뺀 종목
    windows_json    TEXT NOT NULL,           -- 창별 최악 구간
    skipped_json    TEXT NOT NULL,           -- 표본 부족으로 못 본 창
    curve_start     TEXT NOT NULL,
    curve_end       TEXT NOT NULL,
    warnings_json   TEXT NOT NULL,
    calc_version    INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stress_runs_recent
    ON stress_runs (market, created_at DESC);
