-- 결정 원장에 `blocked`(사전 위험 점검이 막은 주문) 종류를 더한다 (docs/paper.md 9·11장, docs/infra.md 25.1453).
-- 교차검증: 막힌 결정을 skip·hold_flag 에 섞으면 선정 비교가 오염되고, 같은 키로 B 규칙의 보유 행을 덮었다.
-- SQLite 는 CHECK 를 고칠 수 없어 표를 다시 만든다. 0071 과 같은 배치 실행에서 이어 적용되므로 대개 빈 표다 — 그래도 행을 옮긴다.
CREATE TABLE IF NOT EXISTS paper_decisions_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   TEXT NOT NULL REFERENCES paper_accounts (id),
    market       TEXT NOT NULL,
    decided_on   TEXT NOT NULL,
    stock_id     INTEGER NOT NULL REFERENCES stocks (id),
    kind         TEXT NOT NULL CHECK (kind IN ('buy', 'skip', 'sell', 'hold_flag', 'missed', 'blocked')),
    reason       TEXT NOT NULL,
    detail_json  TEXT,
    ref_close    REAL,
    ref_date     TEXT,
    ret_5d       REAL,
    ret_20d      REAL,
    ret_60d      REAL,
    bench_5d     REAL,
    bench_20d    REAL,
    bench_60d    REAL,
    outcome_note TEXT,
    outcome_at   TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE (account_id, market, decided_on, stock_id, kind)
);
INSERT INTO paper_decisions_new (id, account_id, market, decided_on, stock_id, kind, reason, detail_json, ref_close, ref_date,
    ret_5d, ret_20d, ret_60d, bench_5d, bench_20d, bench_60d, outcome_note, outcome_at, created_at)
SELECT id, account_id, market, decided_on, stock_id, kind, reason, detail_json, ref_close, ref_date,
    ret_5d, ret_20d, ret_60d, bench_5d, bench_20d, bench_60d, outcome_note, outcome_at, created_at FROM paper_decisions;
DROP TABLE paper_decisions;
ALTER TABLE paper_decisions_new RENAME TO paper_decisions;
CREATE INDEX IF NOT EXISTS idx_paper_decisions_pending ON paper_decisions (decided_on) WHERE ret_60d IS NULL AND outcome_note IS NULL;
