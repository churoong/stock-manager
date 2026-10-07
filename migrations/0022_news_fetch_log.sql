-- 뉴스 수집 진행 상태 (docs/sentiment.md 1장). 종목마다 마지막으로 피드를 받은 시각.
--
-- 미국 뉴스는 나스닥 종목별 RSS 인데 robots.txt 가 요청 간 30초를 요구한다. Actions 에서 150종목을 차례로
-- 받으면 매일 75분(월 1,600분 이상)이라 한도를 넘긴다. 그래서 cron-job.org 가 웹 경로를 1분마다 불러
-- **한 번에 한 종목**씩 받는다(사용자 결정 2026-09-17). 이 표가 "다음은 어느 종목인가" 를 정한다.

CREATE TABLE IF NOT EXISTS news_fetch_log (
    stock_id          INTEGER PRIMARY KEY REFERENCES stocks (id),
    last_fetched_at   TEXT NOT NULL,
    last_status       TEXT NOT NULL,        -- ok / http:403 / parse-error ...
    items_seen        INTEGER NOT NULL DEFAULT 0,
    items_new         INTEGER NOT NULL DEFAULT 0
);
