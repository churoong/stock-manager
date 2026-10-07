-- 재무 데이터와 시점 스냅샷.
--
-- 백테스트가 미래 정보를 보지 못하게 막는 토대다.
-- 2025 사업보고서는 2026-03-10 에 접수됐다. 2026-03-09 에는 알 수 없었다.
-- 그래서 "언제 알 수 있게 됐는가"(as_of_date)를 반드시 함께 저장한다.

CREATE TABLE IF NOT EXISTS financials (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id             INTEGER NOT NULL REFERENCES stocks (id),
    fiscal_year          INTEGER NOT NULL,
    report_code          TEXT NOT NULL,     -- 11011 사업 11012 반기 11013 1분기 11014 3분기
    period_type          TEXT NOT NULL,     -- A 연간, Q 분기
    consolidated         INTEGER NOT NULL,  -- 1 연결(CFS), 0 별도(OFS)

    -- 언제 알 수 있게 됐는가. rcept_no 앞 8자리에서 뽑는다.
    report_date          TEXT NOT NULL,     -- YYYY-MM-DD
    receipt_no           TEXT NOT NULL,     -- 원본 접수번호

    accounting_standard  TEXT,              -- K-IFRS 등
    currency             TEXT NOT NULL,
    unit                 TEXT NOT NULL,     -- 원본 단위. DART 는 원 단위로 준다

    -- 재무상태표
    current_assets       INTEGER,
    noncurrent_assets    INTEGER,
    total_assets         INTEGER,
    current_liabilities  INTEGER,
    noncurrent_liabilities INTEGER,
    total_liabilities    INTEGER,
    capital_stock        INTEGER,
    retained_earnings    INTEGER,
    total_equity         INTEGER,

    -- 손익계산서
    revenue              INTEGER,
    operating_income     INTEGER,
    pretax_income        INTEGER,
    net_income           INTEGER,
    comprehensive_income INTEGER,

    source               TEXT NOT NULL,
    fetched_at           TEXT NOT NULL,
    UNIQUE (stock_id, fiscal_year, report_code, consolidated)
);

CREATE INDEX IF NOT EXISTS idx_financials_stock
    ON financials (stock_id, fiscal_year DESC, report_code);

-- 발표일 기준으로 조회하기 위한 인덱스.
-- 백테스트가 "이 날짜에 알 수 있던 재무" 를 물을 때 쓴다.
CREATE INDEX IF NOT EXISTS idx_financials_report_date
    ON financials (stock_id, report_date DESC);

-- 시점 스냅샷.
-- financials 는 같은 회계연도를 나중에 정정하면 덮어쓴다.
-- 이 표는 덮어쓰지 않고 쌓는다. 백테스트는 오직 이 표만 읽는다.
CREATE TABLE IF NOT EXISTS financial_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id     INTEGER NOT NULL REFERENCES stocks (id),
    as_of_date   TEXT NOT NULL,     -- 이 값을 알 수 있게 된 날 = 접수일
    receipt_no   TEXT NOT NULL,
    fiscal_year  INTEGER NOT NULL,
    report_code  TEXT NOT NULL,
    consolidated INTEGER NOT NULL,
    payload      TEXT NOT NULL,     -- JSON. 그때 받은 값 그대로
    source       TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    UNIQUE (stock_id, receipt_no, consolidated)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_asof
    ON financial_snapshots (stock_id, as_of_date DESC);

-- 공시 목록
CREATE TABLE IF NOT EXISTS disclosures (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id       INTEGER REFERENCES stocks (id),
    corp_code      TEXT NOT NULL,
    receipt_no     TEXT NOT NULL,
    title          TEXT NOT NULL,
    disclosed_at   TEXT NOT NULL,   -- YYYY-MM-DD
    url            TEXT,
    report_name    TEXT,
    is_material    INTEGER NOT NULL DEFAULT 0,
    source         TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    UNIQUE (receipt_no)
);

CREATE INDEX IF NOT EXISTS idx_disclosures_stock
    ON disclosures (stock_id, disclosed_at DESC);

-- 실적 발표 일정
CREATE TABLE IF NOT EXISTS earnings_calendar (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id       INTEGER NOT NULL REFERENCES stocks (id),
    event_type     TEXT NOT NULL,   -- 실적발표 배당락 주총
    scheduled_date TEXT NOT NULL,
    is_confirmed   INTEGER NOT NULL DEFAULT 0,
    note           TEXT,
    source         TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    UNIQUE (stock_id, event_type, scheduled_date)
);

-- 종목과 DART 고유번호를 잇는다.
-- DART 는 종목코드가 아니라 고유번호로 조회한다.
ALTER TABLE stocks ADD COLUMN dart_corp_code TEXT;

CREATE INDEX IF NOT EXISTS idx_stocks_dart ON stocks (dart_corp_code);
