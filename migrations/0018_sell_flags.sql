-- 매도 플래그 (docs/sell_flags.md, Step 13). 표시와 알림만. 자동 매도 없음 (CLAUDE.md 절대 규칙).
--
-- 날짜별로 쌓는다. 그날 조건을 만족한 플래그만 그날 행이 생기고, 조건이 풀리면 이전 행이 is_active=0 이 된다.
-- 같은 (종목, 사유) 가 이어지는 동안 first_seen_date 와 dismissed_at 을 이어받는다.
-- 사용자가 "확인" 을 누르면 dismissed_at 이 찍혀 리포트에서 빠진다(조건이 풀렸다 다시 걸리면 새로 알린다).

CREATE TABLE IF NOT EXISTS sell_flags (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id         INTEGER NOT NULL REFERENCES stocks (id),
    as_of_date       TEXT NOT NULL,
    level            TEXT NOT NULL CHECK (level IN ('green', 'red', 'yellow')),
    reason_code      TEXT NOT NULL,          -- 목표도달 / 손절 / 재무악화 / 기간초과 / 감성급락 / 점검
    rationale_text   TEXT NOT NULL,
    rationale_data   TEXT NOT NULL,          -- JSON {criteria: [...]} 근거표
    is_active        INTEGER NOT NULL DEFAULT 1,
    first_seen_date  TEXT NOT NULL,          -- 이 조건이 처음 걸린 날(이어지는 동안 유지)
    dismissed_at     TEXT,                   -- 사용자가 확인한 시각
    resolved_at      TEXT,                   -- 조건이 풀린 날
    created_at       TEXT NOT NULL,
    UNIQUE (stock_id, as_of_date, reason_code)
);

CREATE INDEX IF NOT EXISTS idx_sell_flags_active ON sell_flags (is_active, as_of_date);
