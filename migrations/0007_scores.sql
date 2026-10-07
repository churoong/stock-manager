-- 팩터와 종합 점수.
--
-- 계산식은 docs/factors.md 가 단일 정의처다. 문서 없는 팩터는 넣지 않는다.
--
-- 두 표로 나눈 이유는 묻는 질문이 다르기 때문이다.
--   factors 는 "이 종목의 밸류가 왜 그 점수인가"에 답한다. 원시 지표와
--            비교 집단, 빠진 지표가 여기 남는다
--   scores  는 "종합 몇 점이고 그때 가중치가 무엇이었나"에 답한다
--
-- 가중치를 바꿔도 과거 행을 덮어쓰지 않는다. calc_version 을 올려 쌓는다.
-- 지난 점수가 어떤 식과 어떤 가중치로 나온 값인지 나중에 알 수 있어야 한다.

CREATE TABLE IF NOT EXISTS factors (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id       INTEGER NOT NULL REFERENCES stocks (id),
    as_of_date     TEXT NOT NULL,     -- 계산 기준일 YYYY-MM-DD
    factor         TEXT NOT NULL,     -- value quality growth momentum risk

    raw_json       TEXT,              -- 원시 지표. 근거 문장이 인용할 실제 수치
    zscore         REAL,              -- 비교 집단 안에서의 z. 결측이면 NULL
    score          REAL,              -- 0~100. 결측이면 NULL

    -- 어느 집단에서 낸 점수인가. market:KOSPI / sector:KOSPI:반도체
    -- 이것이 없으면 점수를 해석할 수 없다. 같은 값도 집단이 다르면 뜻이 다르다.
    peer_group     TEXT NOT NULL,
    peer_size      INTEGER NOT NULL,  -- 그 집단의 종목 수

    missing_fields TEXT,              -- JSON 배열. 왜 점수가 없는지 답한다
    calc_version   INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    UNIQUE (stock_id, as_of_date, factor, calc_version)
);

CREATE INDEX IF NOT EXISTS idx_factors_stock
    ON factors (stock_id, as_of_date DESC, factor);

CREATE INDEX IF NOT EXISTS idx_factors_date
    ON factors (as_of_date, factor, calc_version);

CREATE TABLE IF NOT EXISTS scores (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id              INTEGER NOT NULL REFERENCES stocks (id),
    as_of_date            TEXT NOT NULL,

    total_score           REAL,       -- 산출하지 못했으면 NULL
    factor_scores         TEXT NOT NULL,  -- JSON. 다섯 팩터 점수
    sentiment_score       REAL,       -- -100~+100. Step 7 전까지 NULL
    sentiment_weight_used REAL NOT NULL,

    -- 그때의 가중치 스냅샷. 이것이 있어야 과거 점수를 재현할 수 있다.
    weights_json          TEXT NOT NULL,

    rank_in_market        INTEGER,
    rank_in_sector        INTEGER,

    -- 점수를 내지 않은 사유. "왜 이 종목이 순위에 없나"에 답한다.
    skip_reason           TEXT,

    calc_version          INTEGER NOT NULL,
    created_at            TEXT NOT NULL,
    UNIQUE (stock_id, as_of_date, calc_version)
);

CREATE INDEX IF NOT EXISTS idx_scores_date
    ON scores (as_of_date, total_score DESC);

CREATE INDEX IF NOT EXISTS idx_scores_stock
    ON scores (stock_id, as_of_date DESC);
