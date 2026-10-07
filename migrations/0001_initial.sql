-- Step 1 초기 스키마
--
-- 규칙
--   시각은 전부 UTC ISO 문자열로 저장한다. 표시할 때만 KST·ET 로 바꾼다
--   거래일(trade_date)은 해당 시장의 현지 날짜다
--   외부에서 받아온 테이블에는 source 와 fetched_at 을 반드시 둔다
--   금액 컬럼에는 currency 를 함께 둔다

-- 설정. 단일 사용자이므로 key-value 로 둔다.
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,          -- JSON 문자열
    updated_at  TEXT NOT NULL
);

-- 종목 마스터
CREATE TABLE IF NOT EXISTS stocks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT NOT NULL,        -- 국내는 6자리 단축코드, 미국은 심볼
    market        TEXT NOT NULL,        -- KOSPI KOSDAQ NYSE NASDAQ
    country       TEXT NOT NULL,        -- KR US
    name_ko       TEXT,
    name_en       TEXT,
    sector        TEXT,
    currency      TEXT NOT NULL,        -- KRW USD
    yahoo_symbol  TEXT,                 -- 야후 조회용. 국내는 005930.KS 형태
    status        TEXT NOT NULL DEFAULT 'active',
    source        TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    UNIQUE (ticker, market)
);

-- 일별 시세
CREATE TABLE IF NOT EXISTS prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id    INTEGER NOT NULL REFERENCES stocks (id),
    date        TEXT NOT NULL,          -- 거래일 YYYY-MM-DD (시장 현지 기준)
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL NOT NULL,
    adj_close   REAL,
    volume      INTEGER,
    value       INTEGER,                -- 거래대금
    currency    TEXT NOT NULL,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    UNIQUE (stock_id, date)
);

CREATE INDEX IF NOT EXISTS idx_prices_stock_date ON prices (stock_id, date DESC);

-- 배치 실행 이력
-- 예약 실행은 지연되거나 조용히 건너뛰므로, 예정 시각과 실제 시각을 모두 남긴다.
CREATE TABLE IF NOT EXISTS batch_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name       TEXT NOT NULL,
    market         TEXT,
    trade_date     TEXT,                -- 이 실행이 다루는 거래일
    trigger        TEXT,                -- schedule dispatch manual (0002 에서 trigger_source 로 개명)
    scheduled_for  TEXT,                -- 원래 돌았어야 할 시각
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,       -- running success partial failed skipped
    delay_seconds  INTEGER,             -- 예정 대비 지연
    step_log       TEXT,                -- JSON. 단계별 성공 여부와 건수
    error_text     TEXT
);

CREATE INDEX IF NOT EXISTS idx_batch_runs_job ON batch_runs (job_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_batch_runs_date ON batch_runs (job_name, trade_date, status);

-- 무료 한도 사용량
-- limit_value 가 NULL 이면 한도를 모른다는 뜻이고 state 는 unknown 으로 둔다.
CREATE TABLE IF NOT EXISTS api_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    api_name      TEXT NOT NULL,
    window_type   TEXT NOT NULL,        -- day minute
    window_start  TEXT NOT NULL,
    call_count    INTEGER NOT NULL DEFAULT 0,
    limit_value   INTEGER,
    warn_at_pct   INTEGER NOT NULL DEFAULT 80,
    state         TEXT NOT NULL DEFAULT 'unknown',  -- ok warn blocked unknown
    last_call_at  TEXT,
    updated_at    TEXT NOT NULL,
    UNIQUE (api_name, window_type, window_start)
);
