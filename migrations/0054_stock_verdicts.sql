-- 종목 분석 — 결론 한 줄·근거·반대 목소리 (docs/analysis.md, docs/infra.md 25.1016).
-- 2026-10-08 사용자 제안 "특정종목 분석하는 탭". 일일 배치가 매도 플래그 뒤에 그 시장의 점수가 있는 종목 전부에 대해 만든다.
-- 웹은 계산하지 않고 읽기만 한다(CLAUDE.md). 종목마다 가장 최근 것 한 행.
CREATE TABLE IF NOT EXISTS stock_verdicts (
    stock_id INTEGER PRIMARY KEY REFERENCES stocks (id),
    market TEXT NOT NULL,          -- KR · US (나라)
    verdict TEXT NOT NULL,         -- check_holding · consider_buy · hold · waiting · undecided
    headline TEXT NOT NULL,        -- 결론 한 줄
    detail_json TEXT NOT NULL,     -- {reasons, against, nearest}
    evidence_json TEXT NOT NULL,   -- 근거표 행 [{label, display, threshold, source, as_of}]
    score_as_of TEXT,
    signal_as_of TEXT,
    computed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stock_verdicts_market ON stock_verdicts (market, verdict);
