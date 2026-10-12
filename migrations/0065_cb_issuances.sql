-- 전환사채 발행결정 — 잠재 물량 사실 줄 재료 (docs/analysis.md 55장, docs/factors.md 12.20 후보 3, docs/infra.md 25.1192).
-- 공시 반응 작업이 B(주요사항보고) 목록에서 "전환사채권발행결정" 을 골라 DART `cvbdIsDecsn` 을 접수번호마다 한 번 부른다.
-- 값은 **발행 결정 당시** 것이다 — 그 뒤 전환가 조정·조기상환·전환은 반영하지 않는다
CREATE TABLE IF NOT EXISTS cb_issuances (
    receipt_no TEXT PRIMARY KEY,   -- DART 접수번호 (발행결정 공시)
    stock_id INTEGER NOT NULL REFERENCES stocks (id),
    decided_at TEXT NOT NULL,      -- 공시일 YYYY-MM-DD
    status TEXT NOT NULL,          -- ok · no_row(응답에 그 접수번호 행이 없음 — 다시 부르지 않는다)
    face_amount REAL,              -- 권면총액 (bd_fta, 원)
    conv_price REAL,               -- 전환가액 (cv_prc, 원)
    conv_shares REAL,              -- 전환에 따라 발행할 주식 수 (cvisstk_cnt)
    shares_pct REAL,               -- 발행주식 총수 대비 % (cvisstk_tisstk_vs)
    claim_start TEXT,              -- 전환청구 시작일 (cvrqpd_bgd)
    claim_end TEXT,                -- 전환청구 종료일 (cvrqpd_edd)
    refix_floor REAL,              -- 시가하락 조정 최저가액 (act_mktprcfl_cvprc_lwtrsprc, "-" 이면 NULL)
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cb_issuances_stock ON cb_issuances (stock_id, decided_at);
