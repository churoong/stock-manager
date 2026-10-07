-- 밸류에이션 밴드 (docs/stock_detail.md 2장, Step 10). 종목 상세 화면이 읽는다.
--
-- 신호 계산(batch/jobs/signals.py)도 밴드를 만들지만 장기 후보 300종목만, 저장하지 않고 쓴다.
-- 상세 화면은 어느 종목이든 열 수 있어야 해서 주 1회 유니버스 전 종목을 따로 계산해 둔다.
-- 계산식은 docs/signals.md 1.3절과 같다(PBR 3년 계열, 그때 알 수 있던 자본총계).
--
-- 같은 날 다시 돌리면 덮어쓴다(docs/infra.md 17절). 계산식이 바뀌면 calc_version 을 올린다.

CREATE TABLE IF NOT EXISTS valuation_bands (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id           INTEGER NOT NULL REFERENCES stocks (id),
    as_of_date         TEXT NOT NULL,        -- 계산일
    metric             TEXT NOT NULL,        -- PBR (지금은 이것 하나)

    current_value      REAL,                 -- 마지막 거래일 PBR. 자본이 없거나 음수면 NULL
    p20                REAL,
    p30                REAL,
    p50                REAL,
    p80                REAL,
    sample             INTEGER NOT NULL,     -- 밴드에 쓴 거래일 수. 250 미만이면 분위를 비운다
    band_rank          REAL,                 -- 현재값이 자기 3년 계열의 몇 % 지점인가 (0~100)

    -- 업종 안에서 현재 PBR 이 몇 % 지점인가. 낮을수록 싸다.
    -- 같은 나라·같은 업종(stocks.sector)이 5종목 미만이면 시장 전체로 비교하고 peer_group 에 남긴다.
    peer_percentile    REAL,
    peer_group         TEXT,                 -- sector:KR:금융 / market:KR
    peer_size          INTEGER,

    price_date         TEXT,                 -- 현재값에 쓴 종가의 거래일
    equity_report_date TEXT,                 -- 현재값에 쓴 자본총계의 접수일
    listed_shares      INTEGER,              -- 쓴 상장주식수 (현재 값 하나. 알려진 한계)
    currency           TEXT NOT NULL,
    skip_reason        TEXT,                 -- 밴드를 못 만든 사유. 화면이 "데이터 없음" 옆에 보인다
    calc_version       INTEGER NOT NULL,
    created_at         TEXT NOT NULL,
    UNIQUE (stock_id, as_of_date, metric, calc_version)
);

CREATE INDEX IF NOT EXISTS idx_valuation_bands_stock
    ON valuation_bands (stock_id, as_of_date DESC);
