-- 신호 판정표 (docs/signals.md 9장). "왜 이 종목은 추천에 없나" 에 답한다.
--
-- 확인 가능 원칙의 반대편 절반이다. 추천된 이유(signals.rationale_data)만이 아니라 **안 된 이유**도
-- 확인돼야 완결이다. 유니버스 전 종목 × 기간 3개의 기준별 통과·탈락을 저장한다.
-- 그 나라의 최근 기준일 것만 남긴다(배치가 지난 날짜를 지운다). 900종목 × 3 ≈ 2,700행.
CREATE TABLE IF NOT EXISTS signal_checks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id      INTEGER NOT NULL REFERENCES stocks (id),
    as_of_date    TEXT NOT NULL,
    horizon       TEXT NOT NULL,      -- short mid long
    passed        INTEGER NOT NULL,   -- 1 이면 신호가 났다 (signals 에 같은 행이 있다)
    failed_count  INTEGER NOT NULL,   -- 탈락한 기준 수
    checks_json   TEXT NOT NULL,      -- 근거표와 같은 모양의 행 목록. passed 가 true/false 다
    calc_version  INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (stock_id, as_of_date, horizon)
);

CREATE INDEX IF NOT EXISTS idx_signal_checks_stock ON signal_checks (stock_id, as_of_date DESC);
