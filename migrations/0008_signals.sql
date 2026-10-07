-- 매수 신호와 사이징.
--
-- 규칙은 docs/signals.md 가 단일 정의처다. 문서 없는 규칙은 넣지 않는다.
--
-- **자동 매매 경로는 없다.** 이 표는 화면과 텔레그램이 읽어 보여주기만 한다.
-- 주문을 내는 코드는 이 저장소에 존재하지 않는다.
--
-- 근거를 두 가지로 나눠 저장한다.
--   rationale_text  사람이 읽는 문장. 템플릿 + 실제 수치
--   rationale_data  그 문장이 인용한 수치 원본. 나중에 검증할 수 있게 한다
-- 문장만 남기면 "이 숫자가 어디서 왔나"에 답할 수 없다.

CREATE TABLE IF NOT EXISTS signals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id            INTEGER NOT NULL REFERENCES stocks (id),
    as_of_date          TEXT NOT NULL,
    horizon             TEXT NOT NULL,     -- short mid long
    signal_type         TEXT NOT NULL,     -- 기술적 추세 / 실적 모멘텀 / 밸류에이션 밴드

    -- 권장 매수 구간. "사라" 가 아니라 "이 값 안에서 사라" 여야 한다.
    buy_zone_low        REAL NOT NULL,
    buy_zone_high       REAL NOT NULL,
    currency            TEXT NOT NULL,

    -- 3회 분할 계획. [{step, ratio, price, amount}, ...]
    tranche_plan        TEXT NOT NULL,

    target_price        REAL,
    stop_price          REAL,

    -- 사이징
    suggested_weight_pct REAL,
    suggested_amount     REAL,             -- 최소 단위에 못 미치면 NULL
    size_reduction       REAL NOT NULL,    -- 0~1. 왜 금액이 작은지에 답한다

    -- 섹터 상한을 적용했는지. 업종 데이터가 없으면 적용할 수 없고,
    -- 조용히 넘어가지 않고 사유를 남겨 화면이 표시한다.
    sector_cap_applied  INTEGER NOT NULL DEFAULT 0,
    sector_cap_note     TEXT,

    rationale_text      TEXT NOT NULL,
    rationale_data      TEXT NOT NULL,

    calc_version        INTEGER NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE (stock_id, as_of_date, horizon, calc_version)
);

CREATE INDEX IF NOT EXISTS idx_signals_date
    ON signals (as_of_date, horizon, calc_version);

CREATE INDEX IF NOT EXISTS idx_signals_stock
    ON signals (stock_id, as_of_date DESC);
