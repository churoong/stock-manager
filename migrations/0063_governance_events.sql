-- 공시 위험 이력 1년 — 횡령·배임·불성실공시 (docs/analysis.md 54장, docs/infra.md 25.1157).
-- 금요일 공시 반응 계산이 통째로 바꿔 쓰고 종목 분석이 읽는다. I(거래소공시) 덮은 날이 1년을 잇지 못하면 비어 있다
CREATE TABLE IF NOT EXISTS governance_events (
    receipt_no TEXT PRIMARY KEY,   -- DART 접수번호
    stock_id INTEGER NOT NULL REFERENCES stocks (id),
    day TEXT NOT NULL,             -- 공시일 YYYY-MM-DD
    type TEXT NOT NULL,            -- fraud · unfaithful (services/disclosure_reaction.TYPES)
    title TEXT NOT NULL,
    covered_from TEXT NOT NULL,    -- I 덮은 날이 끊김 없이 이어진 구간
    covered_to TEXT NOT NULL,
    source TEXT NOT NULL,
    computed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_governance_events_stock ON governance_events (stock_id, day);
