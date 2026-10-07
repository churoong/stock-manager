-- 알림 읽음 표시 (docs/intraday.md 7장, 설계서 3.4 alerts.is_read). 2026-09-17 까지 열이 없어
-- 알림 센터가 200건을 그냥 나열했다. 읽음은 사용자 입력이라 배치가 건드리지 않는다.
ALTER TABLE alerts ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0;
