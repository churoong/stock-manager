-- "지금 분석" 요청 (docs/analysis.md 8장, docs/infra.md 25.1018). 2026-10-08 사용자 결정 — 종목 화면의 단추가 관심 종목에 넣고
-- `analyze-stock.yml` 을 깨운다. 같은 종목을 몇 분 안에 다시 누르면 다시 깨우지 않는다(이 표로 본다). 작업이 상태를 바꾼다.
CREATE TABLE IF NOT EXISTS analysis_requests (
    stock_id INTEGER PRIMARY KEY REFERENCES stocks (id),
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL,        -- requested · running · done · failed
    finished_at TEXT,
    note TEXT                    -- 실패 사유
);
