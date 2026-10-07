-- 무응답 감시 기록 (docs/health.md, Step 17).
--
-- 배치가 "실패" 를 알리는 것과 다르다. 여기 남는 것은 **아무 일도 일어나지 않았다** 는 사실이다.
-- 실패 알림은 배치가 보내고, 안 도는 것은 배치가 알릴 수 없어 웹 경로가 본다.
--
-- UNIQUE 가 하루 한 번을 강제한다. 감시는 1시간마다 도는데 제약이 없으면 같은 사실을
-- 하루 열 통 보낸다. 제약을 코드 대신 DB 에 두는 이유는 장중 알림(alerts)과 같다 —
-- 경로가 두 번 겹쳐 돌아도 두 통이 되지 않는다.

CREATE TABLE IF NOT EXISTS health_alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job         TEXT NOT NULL,            -- batch_runs.job_name 과 같은 값. 달력 알림은 'calendar'
    market      TEXT NOT NULL,            -- KR / US
    local_date  TEXT NOT NULL,            -- 그 시장의 현지 날짜. 하루 한 번의 기준
    -- missing(기대 시각까지 성공 없음) / calendar(세션 바닥남) / calendar-low(곧 바닥난다, 25.104).
    -- calendar 와 calendar-low 가 **다른 값**이어야 한다. 하나였다면 미리 보낸 [주의] 가
    -- UNIQUE 에 걸려 정작 멈춘 날의 [무응답] 을 삼킨다 (같은 job·market·날짜다)
    kind        TEXT NOT NULL,
    message     TEXT NOT NULL,            -- 보낸 문구 그대로. 나중에 무엇을 알렸는지 확인할 수 있어야 한다
    data        TEXT NOT NULL,            -- JSON: 마감 시각·마지막 성공·판단 근거
    created_at  TEXT NOT NULL,
    sent_at     TEXT,                     -- 텔레그램 발송 시각. 비어 있으면 다음 호출이 다시 보낸다
    UNIQUE (job, market, local_date, kind)
);

CREATE INDEX IF NOT EXISTS idx_health_alerts_recent ON health_alerts (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_health_alerts_pending ON health_alerts (sent_at, created_at);

-- 감시가 실제로 돌고 있는지. 장중 경로의 cron_heartbeats 와 같은 방식으로 한 줄을 덮어쓴다.
-- 이 행이 오래 멈춰 있으면 cron-job.org 작업이나 헤더를 의심한다 (docs/health.md 1장).
INSERT OR IGNORE INTO cron_heartbeats (job, market, called_at, outcome, detail, calls_today, day)
VALUES ('health', 'ALL', '1970-01-01T00:00:00Z', 'never', '{}', 0, '1970-01-01');
