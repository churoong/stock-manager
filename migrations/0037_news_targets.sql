-- 뉴스 수집 후보 (docs/infra.md 24절, docs/sentiment.md 1장).
--
-- **왜 표로 두나.** 미국 뉴스 수집 경로는 1분마다 돈다. 그 안에서 "점수 상위 N 종목" 을 고르려면
-- scores 를 두 번 훑어야 했고(최신 기준일 찾기 + 그 날짜로 거르기), 하루 1,440번이면 읽은 행이
-- 수천만 행이 된다. 2026-09-18 에 읽기 한도(월 5억)를 넘겨 계정이 막힌 원인의 하나다.
--
-- 후보는 하루에 한 번만 바뀐다(점수는 일일 배치가 낸다). 그래서 배치가 미리 만들어 두고
-- 웹은 이 작은 표만 읽는다. 보유·관심처럼 그때그때 바뀌는 것은 웹이 작은 표에서 직접 합친다.

CREATE TABLE IF NOT EXISTS news_targets (
    market     TEXT NOT NULL,           -- KR / US
    stock_id   INTEGER NOT NULL REFERENCES stocks (id),
    rank       INTEGER,                 -- 점수 순위 (1 등이 가장 높다). 점수 없이 들어온 종목은 NULL
    reason     TEXT NOT NULL,           -- score / holding / watch / monitor
    built_at   TEXT NOT NULL,
    PRIMARY KEY (market, stock_id)
);
