-- 종목별 과거 성과 지표.
--
-- 계산식은 docs/metrics.md 에 있다. 계산식이 바뀌면 calc_version 을 올리고
-- 새 행을 쌓는다. 과거 행을 덮어쓰지 않는다. 어떤 식으로 계산한 값인지
-- 나중에 알 수 있어야 한다.

CREATE TABLE IF NOT EXISTS performance_metrics (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id             INTEGER NOT NULL REFERENCES stocks (id),
    as_of_date           TEXT NOT NULL,     -- 계산 기준일
    window               TEXT NOT NULL,     -- 1Y 3Y 5Y

    cagr                 REAL,
    mdd                  REAL,              -- 음수. 35% 하락은 -0.35
    mdd_peak_date        TEXT,
    mdd_trough_date      TEXT,
    mdd_recovery_days    INTEGER,           -- 미회복이면 NULL
    volatility_ann       REAL,
    sharpe               REAL,
    sortino              REAL,
    beta                 REAL,

    benchmark            TEXT,              -- 베타 계산에 쓴 지수
    risk_free_rate_used  REAL,              -- 그때 쓴 무위험수익률
    data_points          INTEGER NOT NULL,  -- 계산에 쓴 거래일 수
    calc_version         INTEGER NOT NULL,

    created_at           TEXT NOT NULL,
    UNIQUE (stock_id, as_of_date, window, calc_version)
);

CREATE INDEX IF NOT EXISTS idx_metrics_stock
    ON performance_metrics (stock_id, as_of_date DESC, window);

-- 스크리너가 리스크 지표 범위로 거를 때 쓴다
CREATE INDEX IF NOT EXISTS idx_metrics_window
    ON performance_metrics (as_of_date, window, calc_version);
