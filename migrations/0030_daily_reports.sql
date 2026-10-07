-- 일일 리포트 이력 (docs/design.md 3.4, docs/reports.md). CLAUDE.md "배치 결과는 daily_reports 에 저장.
-- 웹앱은 오늘 리포트와 이전 리포트 이력을 보여준다" — 2026-09-17 까지 표가 없었다.
--
-- summary_text 는 텔레그램으로 보낸 본문 그대로다. 화면과 텔레그램이 같은 글을 읽는다.
-- report_items 는 그 본문을 만든 재료(종목·배분·제외 사유·플래그·경고)를 구조로 남긴 것이다.
-- 같은 (market, trade_date) 를 다시 돌리면 지우고 다시 넣는다. 실패한 실행은 넣지 않으므로
-- 마지막 성공 리포트가 남는다 ("배치 실패 시 마지막 성공 리포트 유지").

CREATE TABLE IF NOT EXISTS daily_reports (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    market               TEXT NOT NULL,          -- KR · US
    trade_date           TEXT NOT NULL,          -- 리포트가 다루는 거래일 (전일 데이터 기준)
    status               TEXT NOT NULL,          -- success · partial (경고 있음)
    generated_at         TEXT NOT NULL,
    sent_at              TEXT,                   -- 텔레그램 발송 시각. 실패했으면 NULL
    telegram_message_id  TEXT,                   -- 여러 조각이면 쉼표로 잇는다
    summary_text         TEXT NOT NULL,          -- 보낸 본문 그대로
    warnings_json        TEXT NOT NULL,          -- 경고 목록 JSON
    batch_run_id         INTEGER REFERENCES batch_runs (id),
    UNIQUE (market, trade_date)
);

CREATE TABLE IF NOT EXISTS report_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id       INTEGER NOT NULL REFERENCES daily_reports (id) ON DELETE CASCADE,
    section         TEXT NOT NULL,   -- recommend(1부 종목) · buy_signal(2부 배분) · sell_flag · notice(제외 사유·경고·국면)
    stock_id        INTEGER REFERENCES stocks (id),
    rank            INTEGER NOT NULL,
    payload_json    TEXT NOT NULL,   -- 절의 재료 그대로 (docs/reports.md 2장)
    rationale_text  TEXT
);

CREATE INDEX IF NOT EXISTS idx_report_items_report ON report_items (report_id, section, rank);
