-- 종목 유니버스와 거래소 캘린더.
--
-- 유니버스는 주 1회 스냅샷으로 남긴다. 제외된 종목도 사유와 함께 남긴다.
-- "왜 이 종목이 빠졌는가"를 나중에 물을 수 있어야 하기 때문이다.

CREATE TABLE IF NOT EXISTS universe_members (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date     TEXT NOT NULL,       -- 이 스냅샷의 기준일 YYYY-MM-DD
    stock_id          INTEGER NOT NULL REFERENCES stocks (id),
    included          INTEGER NOT NULL,    -- 1 편입, 0 제외
    exclude_reason    TEXT,                -- 제외 사유. 편입이면 NULL
    market_cap        INTEGER,             -- 판정에 쓴 시가총액
    avg_turnover_20d  INTEGER,             -- 판정에 쓴 20일 평균 거래대금
    listed_days       INTEGER,             -- 판정에 쓴 상장 경과일
    currency          TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    UNIQUE (snapshot_date, stock_id)
);

CREATE INDEX IF NOT EXISTS idx_universe_snapshot
    ON universe_members (snapshot_date, included);

-- 거래소 캘린더.
-- 라이브러리가 판정 근거이고, 이 표는 그 판정을 기록해 나중에 추적하기 위한 것이다.
-- 임시공휴일이 늦게 반영되는 경우를 사후에 찾아낼 수 있어야 한다.
CREATE TABLE IF NOT EXISTS market_calendar (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange    TEXT NOT NULL,             -- XKRX XNYS
    date        TEXT NOT NULL,             -- YYYY-MM-DD 현지 기준
    is_open     INTEGER NOT NULL,
    open_utc    TEXT,
    close_utc   TEXT,
    note        TEXT,
    source      TEXT NOT NULL,             -- 판정에 쓴 라이브러리와 버전
    fetched_at  TEXT NOT NULL,
    UNIQUE (exchange, date)
);

CREATE INDEX IF NOT EXISTS idx_market_calendar_date
    ON market_calendar (exchange, date);

-- 종목 마스터에 유니버스 판정에 필요한 항목을 더한다.
-- SQLite 는 ALTER TABLE ADD COLUMN 만 지원하므로 한 줄씩 붙인다.
ALTER TABLE stocks ADD COLUMN listed_date TEXT;
ALTER TABLE stocks ADD COLUMN security_group TEXT;   -- 증권구분. 주권, 부동산투자회사 등
ALTER TABLE stocks ADD COLUMN section_type TEXT;     -- 소속부. 관리종목 여부가 여기 나타난다
ALTER TABLE stocks ADD COLUMN share_kind TEXT;       -- 보통주 우선주
ALTER TABLE stocks ADD COLUMN isin TEXT;             -- 표준코드 12자리
ALTER TABLE stocks ADD COLUMN listed_shares INTEGER;
ALTER TABLE stocks ADD COLUMN market_cap INTEGER;
ALTER TABLE stocks ADD COLUMN market_cap_date TEXT;
