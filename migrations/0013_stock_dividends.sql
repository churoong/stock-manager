-- 배당 (DART 배당에 관한 사항, docs/data-sources.md 1.1).
--
-- 장기 적립 종목 공개의 선행 조건이다(사용자 결정 2026-09-17: 배당 수집·검증 뒤 공개).
--
-- 같은 사업연도 값을 여러 보고서가 싣는다(당기·전기·전전기). 나중 보고서가 고쳐 적을 수 있어
-- 보고서마다 따로 남긴다(report_year). 과거 시점 판정은 그때까지 접수된 행만 쓴다.
--
-- 단위를 원으로 맞춰 저장한다. 원문은 총액·순이익이 백만원, 주당값이 원, 성향·수익률이 % 다.

CREATE TABLE IF NOT EXISTS stock_dividends (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id                INTEGER NOT NULL REFERENCES stocks (id),
    fiscal_year             INTEGER NOT NULL,
    report_year             INTEGER NOT NULL,     -- 이 값을 실어 온 사업보고서의 연도
    receipt_no              TEXT NOT NULL,
    as_of_date              TEXT,                 -- 접수일. 이 날부터 알 수 있었다

    cash_dividend_total     INTEGER,              -- 원. 연속성·감소 판정은 이것으로 한다(액면분할 영향 없음)
    dps_common              INTEGER,              -- 원. 액면분할이 있으면 해마다 비교할 수 없다
    dps_preferred           INTEGER,
    payout_ratio            REAL,                 -- %
    payout_basis            TEXT,                 -- 연결 / 별도
    yield_common            REAL,                 -- %
    net_income_consolidated INTEGER,              -- 원
    eps_consolidated        INTEGER,
    face_value              INTEGER,

    source                  TEXT NOT NULL,
    fetched_at              TEXT NOT NULL,
    UNIQUE (stock_id, fiscal_year, report_year)
);

CREATE INDEX IF NOT EXISTS idx_stock_dividends_stock
    ON stock_dividends (stock_id, fiscal_year DESC);
