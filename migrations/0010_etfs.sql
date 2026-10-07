-- ETF 장기 적립 추천.
--
-- 규칙은 docs/etf.md 8장이 단일 정의처다.
--
-- 종목(stocks)과 표를 나눈 이유:
--   stocks 에 넣으면 일일 배치·유니버스·스코어가 ETF 를 보통주 잣대로 판정한다.
--   ETF 는 시총·재무가 없고 보는 것이 다르다(보수·순자산·설정일). 섞이면 두 쪽
--   모두 조건문이 늘고, 한쪽을 고칠 때 다른 쪽이 조용히 깨진다.
--
-- 세 표로 나눈다.
--   etfs          마스터. 목록에 있는 ETF
--   etf_profiles  그 시점에 받은 기본 정보. 날짜별로 쌓는다. 보수가 바뀐 이력이 남는다
--   etf_picks     판정 결과. 통과한 것과 뺀 것을 모두 남긴다. "왜 없나" 에 답하려면
--                 뺀 것도 사유와 함께 있어야 한다

CREATE TABLE IF NOT EXISTS etfs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,        -- 거래소 표기. BRK.B 식
    country     TEXT NOT NULL,        -- KR US
    name        TEXT NOT NULL,
    exchange    TEXT,
    yahoo_symbol TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    UNIQUE (symbol, country)
);

CREATE TABLE IF NOT EXISTS etf_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    etf_id          INTEGER NOT NULL REFERENCES etfs (id),
    as_of_date      TEXT NOT NULL,

    category        TEXT,             -- 야후 분류(모닝스타 분류명). 넓은 지수 판정에 쓴다
    family          TEXT,             -- 운용사
    expense_ratio   REAL,             -- 소수. 0.0003 = 0.03%
    total_assets    REAL,             -- 통화 단위 그대로
    currency        TEXT NOT NULL,
    inception_date  TEXT,             -- YYYY-MM-DD
    avg_volume      REAL,
    prev_close      REAL,
    turnover_est    REAL,             -- avg_volume × prev_close. 추정치다

    -- 상위 보유종목 [{symbol, name, pct}]. 야후는 상위 10개만 준다.
    holdings_json   TEXT,
    stock_position  REAL,
    bond_position   REAL,

    source          TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    UNIQUE (etf_id, as_of_date)
);

CREATE TABLE IF NOT EXISTS etf_picks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    etf_id          INTEGER NOT NULL REFERENCES etfs (id),
    as_of_date      TEXT NOT NULL,

    bucket          TEXT,             -- 미국 주식 / 해외 주식 / 세계 주식 / 채권. 제외면 NULL 일 수 있다
    passed          INTEGER NOT NULL, -- 1 통과, 0 제외
    excluded_reason TEXT,             -- 제외 사유. 통과면 NULL

    score           REAL,             -- 0~100. 같은 분류 안의 순위. 제외면 NULL
    rank_in_category INTEGER,
    category_size   INTEGER,          -- 그 분류에서 통과한 개수. 순위를 읽으려면 필요하다

    rationale_text  TEXT NOT NULL,
    -- {criteria: [...], overlap: {...}, weights: {...}}
    -- criteria 는 종목 추천(signals)과 같은 모양이라 화면이 같은 표로 그린다
    rationale_data  TEXT NOT NULL,

    calc_version    INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (etf_id, as_of_date, calc_version)
);

CREATE INDEX IF NOT EXISTS idx_etf_picks_date
    ON etf_picks (as_of_date, passed, bucket);

CREATE INDEX IF NOT EXISTS idx_etf_profiles_etf
    ON etf_profiles (etf_id, as_of_date DESC);
