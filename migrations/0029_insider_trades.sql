-- 내부자 매매 (docs/signals.md 8장, docs/data-sources.md 16절).
--
-- 임원·주요주주의 자사주 매매 보고. 추천 근거표의 참고 행이 읽는다. 팩터에는 넣지 않는다.
-- 보고서 한 건이 한 행이고 원문은 저장하지 않는다. 출처가 DART 든 SEC 든 같은 열이다.
-- 2026-09-17 표만 먼저 만든다. 수집기는 API 스펙을 실측한 뒤에 (16.1 절 [확인필요]).

CREATE TABLE IF NOT EXISTS insider_trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id     INTEGER NOT NULL REFERENCES stocks (id),
    filed_date   TEXT NOT NULL,      -- 접수일 YYYY-MM-DD. "언제 알 수 있게 됐는가" — 시점 규칙의 기준
    trade_date   TEXT,               -- 거래일. 보고서에 없으면 NULL
    insider      TEXT NOT NULL,      -- 보고자 이름 (공시된 그대로)
    role         TEXT,               -- 직위·관계 (등기임원, 주요주주 등). 없으면 NULL
    action       TEXT NOT NULL,      -- buy · sell · other (증여·상속·스톡옵션 행사 등은 other)
    shares       INTEGER NOT NULL,   -- 주식수 (양수). 부호는 action 이 진다
    price        REAL,               -- 단가. 보고서에 없으면 NULL
    currency     TEXT NOT NULL,      -- KRW · USD
    source       TEXT NOT NULL,      -- dart_opendart · sec_edgar
    receipt_no   TEXT NOT NULL,      -- 접수번호·accession. 같은 보고서를 두 번 넣지 않는다
    fetched_at   TEXT NOT NULL,
    UNIQUE (source, receipt_no, insider, action, shares)
);

CREATE INDEX IF NOT EXISTS idx_insider_stock_filed
    ON insider_trades (stock_id, filed_date DESC);
