-- 외부 크론 호출 기록 (docs/intraday.md 7장). 경로마다·시장마다 마지막 호출 한 줄.
--
-- 장 밖 호출은 알림을 만들지 않아 흔적이 남지 않았다. cron-job.org 가 실제로 부르는지,
-- 토큰이 맞는지(여기에 줄이 생기면 맞은 것이다) 를 DB 로 확인하려고 둔다 (2026-09-17).
-- 토큰이 틀린 호출은 기록하지 않는다. 아무나 이 표를 채울 수 없게 하기 위해서다.

CREATE TABLE IF NOT EXISTS cron_heartbeats (
    job           TEXT NOT NULL,           -- intraday
    market        TEXT NOT NULL,           -- KR / US
    called_at     TEXT NOT NULL,           -- UTC
    outcome       TEXT NOT NULL,           -- skipped:장 밖 / checked / error
    detail        TEXT,                    -- JSON 요약 (대상 수·새 알림 수·오류)
    calls_today   INTEGER NOT NULL DEFAULT 1,
    day           TEXT NOT NULL,           -- KST 날짜. 바뀌면 calls_today 를 1 로
    PRIMARY KEY (job, market)
);
