-- 매매 기록 · 보유 · 배당 수령 · 포트폴리오 평가 (docs/portfolio.md, Step 12).
--
-- trades 와 dividend_receipts 는 **사용자 입력 원본**이다. 시스템이 고치지 않는다(CLAUDE.md 매매 규칙).
-- 나머지(trade_lots · positions · portfolio_values · portfolio_summary)는 파생이다. batch/jobs/portfolio.py 가
-- 원본에서 통째로 다시 만든다. 지우고 다시 만들어도 같은 결과가 나와야 한다.

CREATE TABLE IF NOT EXISTS trades (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id              INTEGER NOT NULL REFERENCES stocks (id),
    side                  TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    trade_date            TEXT NOT NULL,              -- 체결일 YYYY-MM-DD
    price                 REAL NOT NULL CHECK (price > 0),   -- 종목 통화 기준 체결가
    quantity              REAL NOT NULL CHECK (quantity > 0),
    currency              TEXT NOT NULL,              -- KRW / USD
    fx_rate               REAL NOT NULL CHECK (fx_rate > 0), -- 1 통화단위당 원. 원화 종목은 1
    fx_rate_source        TEXT NOT NULL,              -- auto (그날 USDKRW 종가) / manual (증권사 체결 환율) / none (원화)
    fee                   REAL,                       -- 종목 통화. 비우면 설정 수수료율로 추정(추정 표시)
    tax                   REAL,                       -- 종목 통화. 비우면 설정 세율로 추정
    horizon               TEXT CHECK (horizon IN ('short', 'mid', 'long')),
    memo                  TEXT,

    -- 매수 당시 근거를 얼린다(복기의 기준, design.md 3.5). 매도 행은 비어 있다
    snapshot_as_of        TEXT,                       -- 스냅샷에 쓴 점수·신호의 기준일
    score_at_trade        REAL,
    signal_type_at_trade  TEXT,                       -- 그 기간의 신호가 있었으면 그 종류
    sentiment_at_trade    REAL,
    factor_scores_at_trade TEXT,                      -- JSON

    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_stock_date ON trades (stock_id, trade_date, id);

-- FIFO 매칭 결과. 매도 한 건이 여러 매수와 짝지어지면 여러 행이다
CREATE TABLE IF NOT EXISTS trade_lots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    buy_trade_id      INTEGER NOT NULL REFERENCES trades (id),
    sell_trade_id     INTEGER NOT NULL REFERENCES trades (id),
    stock_id          INTEGER NOT NULL REFERENCES stocks (id),
    quantity          REAL NOT NULL,
    buy_price         REAL NOT NULL,
    sell_price        REAL NOT NULL,
    currency          TEXT NOT NULL,
    buy_fx            REAL NOT NULL,
    sell_fx           REAL NOT NULL,
    cost              REAL NOT NULL,                  -- 종목 통화. 매수대금 + 배분된 매수 수수료
    proceeds          REAL NOT NULL,                  -- 종목 통화. 매도대금 − 배분된 매도 수수료·세금
    realized_pnl      REAL NOT NULL,                  -- 종목 통화 = proceeds − cost
    realized_pnl_krw  REAL NOT NULL,                  -- 원 = proceeds × sell_fx − cost × buy_fx
    price_pnl_krw     REAL NOT NULL,                  -- 원. 주가 손익 = realized_pnl × buy_fx
    fx_pnl_krw        REAL NOT NULL,                  -- 원. 환차손익 = proceeds × (sell_fx − buy_fx)
    fee_total         REAL NOT NULL,                  -- 종목 통화
    tax_total         REAL NOT NULL,                  -- 종목 통화
    cost_estimated    INTEGER NOT NULL DEFAULT 0,     -- 수수료·세금 중 추정값이 섞였나
    holding_days      INTEGER NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trade_lots_stock ON trade_lots (stock_id);

-- 현재 보유와 평가. 평가 기준일의 종가·환율로 낸다
CREATE TABLE IF NOT EXISTS positions (
    stock_id              INTEGER PRIMARY KEY REFERENCES stocks (id),
    quantity              REAL NOT NULL,
    currency              TEXT NOT NULL,
    avg_price             REAL NOT NULL,              -- 종목 통화. 수수료 포함 평균 단가
    avg_fx                REAL NOT NULL,              -- 남은 수량 가중 평균 매수 환율
    cost                  REAL NOT NULL,              -- 종목 통화
    cost_krw              REAL NOT NULL,
    first_buy_date        TEXT NOT NULL,
    horizon               TEXT,
    price_date            TEXT,                       -- 평가에 쓴 종가의 날짜
    close                 REAL,
    fx_date               TEXT,
    fx_now                REAL,
    market_value          REAL,                       -- 종목 통화
    market_value_krw      REAL,
    unrealized_pnl        REAL,                       -- 종목 통화
    unrealized_pnl_krw    REAL,
    unrealized_price_pnl_krw REAL,
    unrealized_fx_pnl_krw REAL,
    weight_pct            REAL,                       -- 평가액 기준 포트폴리오 비중
    updated_at            TEXT NOT NULL
);

-- 배당 수령 (사용자 입력)
CREATE TABLE IF NOT EXISTS dividend_receipts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id          INTEGER NOT NULL REFERENCES stocks (id),
    pay_date          TEXT NOT NULL,
    amount_per_share  REAL,
    quantity          REAL,
    gross_amount      REAL NOT NULL CHECK (gross_amount >= 0),  -- 종목 통화, 세전
    tax               REAL NOT NULL DEFAULT 0,                  -- 종목 통화, 원천징수
    net_amount        REAL NOT NULL,                            -- 종목 통화, 실수령
    currency          TEXT NOT NULL,
    fx_rate           REAL NOT NULL CHECK (fx_rate > 0),
    fx_rate_source    TEXT NOT NULL,
    memo              TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dividend_receipts_stock ON dividend_receipts (stock_id, pay_date);

-- 날짜별 평가액과 시간가중 수익률 지수 (포트폴리오 CAGR·MDD·샤프의 입력)
CREATE TABLE IF NOT EXISTS portfolio_values (
    date              TEXT PRIMARY KEY,
    value_krw         REAL NOT NULL,                  -- 그날 종가·환율로 본 보유 평가액
    net_flow_krw      REAL NOT NULL,                  -- 그날 넣은 돈(+매수) − 뺀 돈(−매도 대금)
    dividends_krw     REAL NOT NULL,                  -- 그날 받은 배당(세후)
    twr_index         REAL NOT NULL,                  -- 시간가중 수익률 지수. 첫날 1
    created_at        TEXT NOT NULL
);

-- 한 줄 요약. 계산 결과를 JSON 으로 담는다(합계·비중·지표·실적 일정·경고)
CREATE TABLE IF NOT EXISTS portfolio_summary (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    as_of_date        TEXT NOT NULL,
    totals_json       TEXT NOT NULL,
    allocation_json   TEXT NOT NULL,
    metrics_json      TEXT NOT NULL,
    upcoming_json     TEXT NOT NULL,
    warnings_json     TEXT NOT NULL,
    trades_version    TEXT NOT NULL,                  -- 계산에 쓴 원본의 지문(행 수·최종 수정 시각). 화면이 "계산 중" 을 판단
    calc_version      INTEGER NOT NULL,
    created_at        TEXT NOT NULL
);
