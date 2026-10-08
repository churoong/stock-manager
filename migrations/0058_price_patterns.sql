-- 비슷한 국면 — 종목마다 자기 과거의 상태별(3개월 수익률 × 52주 고점 근접, 삼분위 3 × 3) 그 뒤 수익 분포
-- (docs/analysis.md 13장, docs/infra.md 25.1039). 주간 성과 지표 작업이 5년 시세를 읽는 김에 함께 내고, 종목마다 가장 최근 것 한 행만 둔다.
--   stats_json  {"since", "until", "edges": {"r3": [a, b], "prox": [c, d]}, "buckets": {"i-j": {"days", "episodes",
--               "h": {"1": {"n", "median", "p05", "p16", "p84", "p95", "up"}, ...}}}, "all": {...}, "current": "i-j"}
CREATE TABLE IF NOT EXISTS price_patterns (
    stock_id    INTEGER PRIMARY KEY REFERENCES stocks (id),
    as_of_date  TEXT NOT NULL,
    stats_json  TEXT NOT NULL,
    computed_at TEXT NOT NULL
);
