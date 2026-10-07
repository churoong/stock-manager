-- 바깥 API 접근토큰 (docs/infra.md 25.983, 2026-10-07 — 한국투자증권 KIS 를 국내 장중 시세로 붙임).
--
-- KIS 접근토큰은 유효 24시간, 발급은 1분에 1회 제한이다. 장중 감시(웹, 5분 크론)가 부를 때마다 받으면 안 되고,
-- Vercel 함수는 상태가 없어 메모리에 둘 수 없다. 그래서 한 줄을 DB 에 둔다. 값은 토큰 자체라 **백업에 넣지 않는다**
-- (`scripts/backup_db.py` ESSENTIAL_TABLES 밖) — 잃어도 다음 호출이 새로 받는다.
CREATE TABLE IF NOT EXISTS api_tokens (
    name TEXT PRIMARY KEY,           -- 'kis'
    token TEXT NOT NULL,
    expires_at TEXT NOT NULL,        -- ISO UTC
    source TEXT NOT NULL,            -- kis_openapi
    fetched_at TEXT NOT NULL
);
