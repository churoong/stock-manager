-- 모의 투자 계좌 (docs/paper.md, docs/infra.md 25.1443, 2026-10-15 사용자 결정).
--
-- 실제 매매(`trades`·`positions`)와 **완전히 따로** 둔다 — 사용자 입력은 입금뿐이고 매매는 배치의 규칙이 한다.
-- 입금 장부와 체결 장부가 원본이다. 지우거나 고치지 않는다(잘못 넣은 입금은 취소 행을 더한다).
-- 보유·현금·날마다 평가는 원본에서 다시 낸다 — 지우고 다시 만들어도 같아야 한다.

-- 계좌. 매수 규칙은 같고 매도 규칙만 다르다 (docs/paper.md 1장)
CREATE TABLE IF NOT EXISTS paper_accounts (
    id            TEXT PRIMARY KEY,        -- A · B
    name          TEXT NOT NULL,
    sell_rule     TEXT NOT NULL,           -- all (모든 매도 플래그) · core (손절·목표도달·기간초과만)
    rule_version  INTEGER NOT NULL,        -- 규칙서 판 (docs/paper.md 2장). 바꿀 때마다 올린다
    created_at    TEXT NOT NULL
);

INSERT OR IGNORE INTO paper_accounts (id, name, sell_rule, rule_version, created_at)
VALUES ('A', '모의 A (모든 매도 플래그)', 'all', 1, '2026-10-15T00:00:00+00:00'),
       ('B', '모의 B (손절·목표·기간만)', 'core', 1, '2026-10-15T00:00:00+00:00');

-- 입금 장부 (사용자 입력 원본). 한 행이 **모든 계좌에** 같은 금액으로 들어간다 (docs/paper.md 1장)
-- 취소는 음수 행을 더하고 원래 행을 가리킨다 — 원래 행은 남는다. 한 입금은 한 번만 취소된다
CREATE TABLE IF NOT EXISTS paper_cash_flows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    flow_date   TEXT NOT NULL,             -- 넣은 날 (한국 날짜 YYYY-MM-DD)
    amount_krw  INTEGER NOT NULL CHECK (amount_krw != 0),
    kind        TEXT NOT NULL CHECK (kind IN ('deposit', 'cancel')),
    cancels_id  INTEGER UNIQUE REFERENCES paper_cash_flows (id),
    memo        TEXT,
    created_at  TEXT NOT NULL,             -- UTC ISO
    request_key TEXT UNIQUE,               -- 화면이 요청마다 만든 키 — 두 번 누름은 같은 키라 한 번만 들어간다 (docs/paper.md 5장)
    CHECK ((kind = 'deposit' AND amount_krw > 0 AND cancels_id IS NULL)
           OR (kind = 'cancel' AND amount_krw < 0 AND cancels_id IS NOT NULL))
);

-- 주문. 아침 리포트를 만든 배치가 내고, 그 시장의 **다음 거래일** 시세로 다음 배치가 체결을 판정한다 (docs/paper.md 2.3)
CREATE TABLE IF NOT EXISTS paper_orders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id     TEXT NOT NULL REFERENCES paper_accounts (id),
    market         TEXT NOT NULL,          -- KR · US
    stock_id       INTEGER NOT NULL REFERENCES stocks (id),
    side           TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type     TEXT NOT NULL CHECK (order_type IN ('limit', 'market')),
    limit_price    REAL,                   -- 종목 통화. 지정가만
    quantity       INTEGER NOT NULL CHECK (quantity > 0),
    currency       TEXT NOT NULL,
    decided_on     TEXT NOT NULL,          -- 주문을 낸 리포트의 거래일(그 시장 현지)
    fill_session   TEXT NOT NULL,          -- 체결을 판정할 거래일 = decided_on 다음 거래일. 정지로 미룬 매도는 앞으로 민다
    fill_basis     TEXT NOT NULL DEFAULT 'open' CHECK (fill_basis IN ('open', 'close')),  -- 장 열린 뒤 낸 주문은 종가
    deferrals      INTEGER NOT NULL DEFAULT 0, -- 정지로 미룬 횟수(거래일). 넘치면 마지막 종가로 청산 (docs/paper.md 2.3)
    tranche        INTEGER NOT NULL DEFAULT 0,  -- 분할 회차 1·2·3. 매도는 0
    reason         TEXT NOT NULL,          -- 매수: 2부 배분 · 매도: 플래그 사유 코드(쉼표)
    horizon        TEXT,                   -- 매수: 신호의 투자 기간(매도 플래그의 목표·손절·기간에 쓴다)
    score_at_order REAL,                   -- 매수: 그때 종합 점수(매도 플래그 '점수 20점 하락' 의 기준점)
    score_as_of    TEXT,                   -- 그 점수의 기준일
    rationale_json TEXT,                   -- 그때의 근거표·설정·비용 스냅샷 (매매 일지)
    rule_version   INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'filled', 'expired')),
    closed_on      TEXT,                   -- 체결·취소를 적은 거래일
    close_note     TEXT,                   -- 왜 안 찼나 (종가가 회차 가격 위 · 현금 부족 등)
    created_at     TEXT NOT NULL,
    UNIQUE (account_id, market, decided_on, stock_id, side, tranche)
);

CREATE INDEX IF NOT EXISTS idx_paper_orders_open ON paper_orders (status, market, fill_session);

-- 체결 장부 (원본). 주문 하나에 체결 하나 — 다시 돌려도 두 번 적히지 않는다
CREATE TABLE IF NOT EXISTS paper_fills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     INTEGER NOT NULL UNIQUE REFERENCES paper_orders (id),
    account_id   TEXT NOT NULL REFERENCES paper_accounts (id),
    stock_id     INTEGER NOT NULL REFERENCES stocks (id),
    side         TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    fill_date    TEXT NOT NULL,            -- 그 시장 현지 거래일
    price        REAL NOT NULL CHECK (price > 0),   -- 종목 통화. 슬리피지 전 체결가(회차 가격 · 시가)
    quantity     INTEGER NOT NULL CHECK (quantity > 0),
    currency     TEXT NOT NULL,
    fx_rate      REAL NOT NULL CHECK (fx_rate > 0), -- 1 통화단위당 원. 원화는 1
    fx_date      TEXT,                     -- 쓴 환율의 날짜. 원화는 비움
    fee          REAL NOT NULL,            -- 종목 통화
    tax          REAL NOT NULL,            -- 종목 통화
    slippage     REAL NOT NULL,            -- 종목 통화. 불리한 쪽으로 얹은 금액
    cash_krw     REAL NOT NULL,            -- 현금에 준 영향(원). 매수 음수 · 매도 양수
    horizon      TEXT,                     -- 주문에서 옮긴다
    score_at_trade REAL,
    snapshot_as_of TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_fills_account ON paper_fills (account_id, fill_date);

-- 날마다 평가와 검산 (파생). 한국 날짜 하루에 한 행 — 국내·미국 배치가 같은 날 두 번 적으면 뒤가 덮는다
CREATE TABLE IF NOT EXISTS paper_daily (
    account_id     TEXT NOT NULL REFERENCES paper_accounts (id),
    as_of          TEXT NOT NULL,          -- 한국 날짜
    invested_krw   REAL NOT NULL,          -- 총 입금액 = 입금 장부 합
    cash_krw       REAL NOT NULL,
    holdings_krw   REAL NOT NULL,          -- 보유 평가액(그날 아는 마지막 종가 · 환율)
    equity_krw     REAL NOT NULL,          -- 평가 총액 = 현금 + 보유 평가
    pnl_krw        REAL NOT NULL,          -- 평가 총액 − 총 입금액
    twr_index      REAL NOT NULL,          -- 시간가중 수익 지수 (첫날 1)
    kospi_krw      REAL,                   -- 같은 날 같은 돈을 KOSPI 에 넣었다면 (그림자 계좌)
    sp500_krw      REAL,                   -- 같은 날 같은 돈을 S&P500 에 넣었다면 (원화 환산)
    positions_json TEXT NOT NULL,          -- 그날 보유 [{stock_id, qty, cost_krw, value_krw, price_date}]
    fills_today    INTEGER NOT NULL DEFAULT 0,
    recon_ok       INTEGER NOT NULL,       -- 장부로 다시 낸 현금이 어제 + 오늘 움직임과 맞나
    recon_note     TEXT,
    unknown_json   TEXT,                   -- 평가에 못 넣은 보유(종가 없음·환율 없음)
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (account_id, as_of)
);
