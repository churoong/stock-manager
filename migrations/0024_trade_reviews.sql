-- 매매 복기 (docs/review.md, Step 15).
--
-- 둘 다 파생이다. batch/jobs/portfolio.py 가 trade_lots 를 다시 만들 때 함께 통째로 다시 만든다.
-- 지우고 다시 돌려도 같은 결과가 나와야 한다.
--
-- 단위는 매수 결정 하나다. FIFO lot 을 그대로 세면 관찰이 부풀어 통계가 왜곡된다.

CREATE TABLE IF NOT EXISTS trade_reviews (
    buy_trade_id          INTEGER PRIMARY KEY REFERENCES trades (id),
    stock_id              INTEGER NOT NULL REFERENCES stocks (id),
    horizon               TEXT,                       -- 매수 시 입력한 투자 기간. 없을 수 있다
    buy_date              TEXT NOT NULL,
    last_sell_date        TEXT NOT NULL,
    currency              TEXT NOT NULL,

    -- 결과 (lot 합계)
    quantity_sold         REAL NOT NULL,
    quantity_bought       REAL NOT NULL,
    partial               INTEGER NOT NULL,           -- 1 이면 일부만 팔았다. 판 만큼만 복기
    cost                  REAL NOT NULL,              -- 종목 통화, 수수료 포함
    proceeds              REAL NOT NULL,              -- 종목 통화, 수수료·세금 제외
    return_pct            REAL NOT NULL,              -- 실수령/원가 − 1
    holding_days          REAL NOT NULL,              -- 수량 가중
    realized_pnl_krw      REAL NOT NULL,
    price_pnl_krw         REAL NOT NULL,
    fx_pnl_krw            REAL NOT NULL,

    -- 매수 당시 근거 (trades 스냅샷 복사). 없으면 NULL 이고 신호별 통계에서 뺀다
    has_snapshot          INTEGER NOT NULL,
    snapshot_as_of        TEXT,
    score_at_trade        REAL,
    signal_type_at_trade  TEXT,
    sentiment_at_trade    REAL,
    factor_scores_at_trade TEXT,                      -- JSON

    -- 판정. 복기 시점의 설정 목표·손절과 견준다
    target_pct            REAL,
    stop_pct              REAL,
    outcome               TEXT NOT NULL,              -- target gain flat loss stop / none(기간 없음)
    verdict_text          TEXT NOT NULL,              -- 템플릿 + 저장된 수치

    calc_version          INTEGER NOT NULL,
    created_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trade_reviews_stock ON trade_reviews (stock_id, last_sell_date DESC);

CREATE TABLE IF NOT EXISTS review_stats (
    group_key             TEXT PRIMARY KEY,           -- all / horizon:long / signal:실적 모멘텀 / signal:없음
    group_kind            TEXT NOT NULL,              -- all horizon signal
    label                 TEXT NOT NULL,
    n                     INTEGER NOT NULL,
    sample_ok             INTEGER NOT NULL,           -- n >= MIN_SAMPLE. 0 이면 단정하지 않는다
    win_rate              REAL,
    avg_return_pct        REAL,                       -- 원가 가중
    median_return_pct     REAL,
    avg_holding_days      REAL,
    target_rate           REAL,                       -- 기간이 있는 것만
    stop_rate             REAL,
    total_pnl_krw         REAL NOT NULL,
    calc_version          INTEGER NOT NULL,
    created_at            TEXT NOT NULL
);
