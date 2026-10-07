-- 증권사 적중률 성적표 (docs/brokers.md, docs/infra.md 25.995). 2026-10-07 사용자 지시("다 진행해" — 차별점 1번).
-- `kr_opinions`(KIS, 25.988) × 우리 시세로 매주 다시 계산한다. 계산 결과라 언제든 다시 만들 수 있다.
CREATE TABLE IF NOT EXISTS broker_stats (
    broker TEXT PRIMARY KEY,
    as_of TEXT NOT NULL,            -- 시세 기준일 (이 날까지의 시세로 쟀다)
    n_opinions INTEGER NOT NULL,    -- 모든 의견
    n_target INTEGER NOT NULL,      -- 목표가가 있고 기준가를 잡은 의견
    avg_upside_pct REAL,            -- 제시 상승여력 평균 (%)
    n_fwd INTEGER NOT NULL,         -- 60거래일이 지난 상향 목표가 의견
    avg_excess_pct REAL,            -- 그 의견들의 60거래일 지수 대비 초과수익 평균 (%p)
    hit_pct REAL,                   -- 초과수익 > 0 비율 (%)
    n_touch INTEGER NOT NULL,       -- 터치 여부가 정해진 상향 목표가 의견
    touch_pct REAL,                 -- 120거래일 안 목표가 터치 비율 (%)
    skipped_action INTEGER NOT NULL,-- 기간 안 분할·병합으로 뺀 의견
    computed_at TEXT NOT NULL
);
