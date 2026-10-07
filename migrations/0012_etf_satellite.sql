-- 위성 ETF 판정 결과 (docs/etf.md 10장).
--
-- 핵심 판정(etf_picks)과 표를 나눈다. 같은 ETF 가 핵심에서는 "좁은 지수" 로 빠지고
-- 위성에서는 통과할 수 있다. 한 표에 두면 한 행의 passed 가 두 뜻을 갖게 된다.
--
-- 위성 묶음 어디에도 들어오지 않는 ETF 는 저장하지 않는다(핵심이거나 범위 밖).

CREATE TABLE IF NOT EXISTS etf_satellite_picks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    etf_id          INTEGER NOT NULL REFERENCES etfs (id),
    as_of_date      TEXT NOT NULL,       -- 판정에 쓴 프로필의 기준일

    sat_group       TEXT NOT NULL,       -- 배당 업종 테마
    sub_group       TEXT NOT NULL,       -- 비교 단위. 섹터 이름·배당 하위 묶음·야후 분류·기초지수
    passed          INTEGER NOT NULL,
    excluded_reason TEXT,

    score           REAL,
    rank_in_group   INTEGER,
    group_size      INTEGER,

    rationale_text  TEXT NOT NULL,
    -- {criteria: [...], warnings: [...], component_scores, weights}
    rationale_data  TEXT NOT NULL,

    calc_version    INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (etf_id, as_of_date, calc_version)
);

CREATE INDEX IF NOT EXISTS idx_etf_satellite_date
    ON etf_satellite_picks (as_of_date, passed, sat_group);
