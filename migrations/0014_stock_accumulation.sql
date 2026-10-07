-- 장기 적립 종목 판정 (docs/accumulation.md).
--
-- 가격을 보지 않는 판정이라 신호(signals)와 표를 나눈다. 게이트에서 빠진 종목도 첫 탈락 게이트와 함께 남긴다.
-- 화면이 "왜 없나" 와 "신규 적립 중단 검토" 를 보여 주려면 빠진 행이 있어야 한다.

CREATE TABLE IF NOT EXISTS stock_accum_picks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id          INTEGER NOT NULL REFERENCES stocks (id),
    as_of_date        TEXT NOT NULL,
    fiscal_year_to    INTEGER NOT NULL,   -- 5개 사업연도 창의 마지막 해

    passed            INTEGER NOT NULL,
    first_failed_gate TEXT,               -- G2 .. G10. 통과면 NULL
    excluded_reason   TEXT,

    score             REAL,               -- 안정성 점수 0~100
    rank_in_group     INTEGER,
    group_size        INTEGER,
    long_signal_on    INTEGER NOT NULL DEFAULT 0,  -- 같은 날 장기 신호도 켜졌는가 (참고, 점수에 안 씀)

    -- 이 실행에서 잰 문턱(G8 중앙값·n 등). 문턱이 실행마다 바뀌므로 재현하려면 필요하다
    thresholds_json   TEXT NOT NULL,
    rationale_text    TEXT NOT NULL,
    rationale_data    TEXT NOT NULL,      -- {criteria, component_scores, metrics}

    calc_version      INTEGER NOT NULL,
    created_at        TEXT NOT NULL,
    UNIQUE (stock_id, as_of_date, calc_version)
);

CREATE INDEX IF NOT EXISTS idx_stock_accum_date
    ON stock_accum_picks (as_of_date, passed, rank_in_group);
