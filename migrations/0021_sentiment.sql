-- 뉴스 감성 (docs/sentiment.md, Step 7). 스키마는 design.md 3.3 절 그대로.
--
-- **원문 본문은 저장하지 않는다**(CLAUDE.md). 제목·주소·발행 시각·점수만.
-- 어느 소스·채점기를 쓸지는 조사 결과로 정한다. 표는 소스와 무관하게 먼저 둔다.

CREATE TABLE IF NOT EXISTS news (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id      INTEGER NOT NULL REFERENCES stocks (id),
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    published_at  TEXT NOT NULL,           -- UTC
    publisher     TEXT,
    lang          TEXT NOT NULL CHECK (lang IN ('ko', 'en')),
    source        TEXT NOT NULL,           -- 수집 경로 (예: rss:연합뉴스, yahoo_rss)
    fetched_at    TEXT NOT NULL,
    UNIQUE (stock_id, url)
);

CREATE INDEX IF NOT EXISTS idx_news_stock_time ON news (stock_id, published_at);

CREATE TABLE IF NOT EXISTS article_sentiments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id        INTEGER NOT NULL REFERENCES news (id),
    score          REAL NOT NULL CHECK (score >= -1 AND score <= 1),
    method         TEXT NOT NULL,          -- vader / 모델 이름 등
    method_version TEXT,
    matched_terms  TEXT,                   -- JSON. 사전 방식이면 걸린 단어, 모델이면 클래스 확률
    created_at     TEXT NOT NULL,
    UNIQUE (news_id, method)
);

CREATE TABLE IF NOT EXISTS sentiment_scores (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id             INTEGER NOT NULL REFERENCES stocks (id),
    as_of_date           TEXT NOT NULL,
    sentiment            REAL,                 -- −100~+100. 기사가 모자라면 NULL (docs/sentiment.md 2장)
    article_count        INTEGER NOT NULL,     -- 30일 창 안 기사 수
    positive_count       INTEGER NOT NULL,
    negative_count       INTEGER NOT NULL,
    negative_count_7d    INTEGER NOT NULL,     -- 감성 급락 판정 입력
    decay_halflife_days  REAL NOT NULL,
    delta_7d             REAL,                 -- 오늘 − 7일 전 (그때 알 수 있던 기사로 낸 값)
    delta_30d            REAL,
    method               TEXT NOT NULL,
    calc_version         INTEGER NOT NULL,
    created_at           TEXT NOT NULL,
    UNIQUE (stock_id, as_of_date)
);
