-- 로그인 시도 기록.
--
-- 짧은 비밀번호를 쓰기로 했으므로 시도 횟수 제한이 방어의 전부다.
-- 웹앱은 서버리스라 인스턴스가 매번 바뀐다. 메모리에 세면 소용이 없어
-- 데이터베이스에 남긴다.
--
-- 주소 원본은 저장하지 않는다. 서명키로 해시한 값만 둔다.

CREATE TABLE IF NOT EXISTS login_attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_hash       TEXT NOT NULL,       -- 요청 주소의 해시. 원본은 남기지 않는다
    attempted_at  TEXT NOT NULL,       -- UTC ISO
    success       INTEGER NOT NULL     -- 0 실패, 1 성공
);

-- 최근 실패를 세는 질의가 전부라 이 순서로 인덱스를 둔다
CREATE INDEX IF NOT EXISTS idx_login_attempts_recent
    ON login_attempts (attempted_at DESC, success);

CREATE INDEX IF NOT EXISTS idx_login_attempts_ip
    ON login_attempts (ip_hash, attempted_at DESC);
