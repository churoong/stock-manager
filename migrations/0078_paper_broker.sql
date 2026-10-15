-- 증권사 모의투자 연결 — 내부 모의 계좌 주문을 KIS 모의투자에 베껴 낸 줄과 날마다의 잔고 (docs/paper.md 15장, docs/infra.md 25.1475).
--
-- 가상 돈이다(KIS 모의투자, 2026-10-15 사용자 제안). 실계좌 주문은 여기 쓰지 않는다. 키가 없으면 이 표들은 비어 있다.
-- 계좌번호는 어디에도 적지 않는다(시크릿에만).

-- 증권사 모의 주문 — 내부 주문 하나에 거래일마다 한 줄(두 번 주문 막기). 보내기 **전에** `sending` 으로 먼저 적고 보낸다 —
-- 응답을 못 받은 줄은 다시 보내지 않고 다음 날 체결 조회로 맞춘다
CREATE TABLE IF NOT EXISTS paper_broker_orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    broker          TEXT NOT NULL,                 -- kis_vts
    paper_order_id  INTEGER REFERENCES paper_orders (id),  -- 베낀 내부 주문. 정리 주문은 비어 있다
    account_id      TEXT NOT NULL REFERENCES paper_accounts (id),  -- 베낀 내부 계좌(기본 B)
    market          TEXT NOT NULL,
    stock_id        INTEGER NOT NULL REFERENCES stocks (id),
    code            TEXT NOT NULL,                 -- 증권사에 보낸 종목 코드
    side            TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type      TEXT NOT NULL CHECK (order_type IN ('limit', 'market')),
    limit_price     REAL,                          -- 보낸 지정가(호가 단위로 맞춘 것). 시장가는 비어 있다
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    trade_date      TEXT NOT NULL,                 -- 주문을 낸 거래일(= 내부 주문의 fill_session)
    reason          TEXT NOT NULL,                 -- 베낌 · 정리
    status          TEXT NOT NULL CHECK (status IN ('sending', 'accepted', 'rejected', 'filled', 'partial', 'unfilled',
                                                    'unknown')),
    broker_order_no TEXT,                          -- KIS 주문번호(ODNO)
    sent_at         TEXT,                          -- 보낸 시각(UTC) — 09:00 KST 앞이면 장전 동시호가
    message         TEXT,                          -- 거부·오류(KIS 오류 코드와 문장)
    filled_qty      INTEGER,
    avg_price       REAL,                          -- 체결 평균가(원)
    source          TEXT NOT NULL,                 -- kis_vts
    fetched_at      TEXT,                          -- 증권사에서 상태를 마지막으로 받은 시각
    created_at      TEXT NOT NULL,
    -- 거래일까지 — 정지로 미룬 매도는 같은 내부 주문이 다음 장에 다시 열린다(fill_session 만 바뀜, 검증 [권장] 4)
    UNIQUE (broker, paper_order_id, trade_date)
);

-- 정리 주문은 내부 주문이 없어 위 유일 제약이 막지 못한다 — 같은 날 같은 종목 한 번
CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_broker_cleanup
    ON paper_broker_orders (broker, trade_date, stock_id, side) WHERE paper_order_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_paper_broker_open ON paper_broker_orders (broker, status, trade_date);

-- 날마다의 증권사 모의 잔고와 내부 장부와 어긋난 것 — 장부 맞추기(docs/paper.md 14장 2)
CREATE TABLE IF NOT EXISTS paper_broker_daily (
    broker        TEXT NOT NULL,
    as_of         TEXT NOT NULL,                   -- 조회한 거래일(국내)
    account_id    TEXT NOT NULL REFERENCES paper_accounts (id),  -- 견준 내부 계좌
    cash_krw      REAL,                            -- 예수금 총금액
    total_krw     REAL,                            -- 총평가금액
    holdings_json TEXT NOT NULL,                   -- [[종목 id 또는 코드, 수량, 평균가]]
    diff_json     TEXT NOT NULL,                   -- {종목 id: [증권사 수량, 내부 수량]} — 어긋난 것만
    source        TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (broker, as_of)
);
