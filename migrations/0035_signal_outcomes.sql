-- 신호 성적표 (docs/signals.md 10장). "그때 추천이 맞았나" 에 답한다.
--
-- signal_outcomes: 신호 하나(종목·기준일·기간)의 그 뒤 수익률. 5·20·60 거래일 뒤 종가 대비, 목표·손절 터치,
-- 같은 구간의 시장 지수 수익률. 창이 아직 안 찬 값은 NULL (앞날을 지어내지 않는다).
-- signal_outcome_stats: 나라·기간·창별 평균·이긴 비율. 웹은 계산하지 않으므로 배치가 낸다.
-- 둘 다 파생이라 배치가 그 나라 것을 지우고 다시 만든다.

CREATE TABLE IF NOT EXISTS signal_outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id        INTEGER NOT NULL REFERENCES stocks (id),
    as_of_date      TEXT NOT NULL,
    horizon         TEXT NOT NULL,
    entry_date      TEXT,             -- 기준일 이후 첫 거래일 (리포트는 장 시작 전에 오므로 그날 종가를 진입가로 본다)
    entry_close     REAL,
    ret_5d          REAL,
    ret_20d         REAL,
    ret_60d         REAL,
    max_up          REAL,             -- 60거래일 안 최고 종가 / 진입가 − 1
    max_down        REAL,             -- 60거래일 안 최저 종가 / 진입가 − 1
    hit_target      INTEGER,          -- 60거래일 안 종가 ≥ 목표가. 목표가 없으면 NULL
    hit_stop        INTEGER,          -- 60거래일 안 종가 ≤ 손절가
    bench_ret_20d   REAL,             -- 같은 날짜의 시장 지수 (index_prices) 수익률
    bench_ret_60d   REAL,
    days_available  INTEGER NOT NULL, -- 진입 뒤 확보된 거래일 수
    computed_at     TEXT NOT NULL,
    UNIQUE (stock_id, as_of_date, horizon)
);

CREATE TABLE IF NOT EXISTS signal_outcome_stats (
    country          TEXT NOT NULL,
    horizon          TEXT NOT NULL,   -- short mid long
    window_days      INTEGER NOT NULL,
    n                INTEGER NOT NULL,
    avg_ret          REAL,
    win_rate         REAL,            -- 수익률 > 0 비율
    avg_excess       REAL,            -- 지수 대비 평균 초과 (둘 다 있는 건만)
    hit_target_rate  REAL,
    hit_stop_rate    REAL,
    since            TEXT,            -- 집계에 든 가장 이른 기준일
    computed_at      TEXT NOT NULL,
    PRIMARY KEY (country, horizon, window_days)
);
