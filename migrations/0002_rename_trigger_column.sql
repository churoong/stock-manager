-- batch_runs.trigger 는 SQLite 예약어라 조회할 때마다 따옴표가 필요하다.
-- 따옴표를 빠뜨리면 구문 오류가 나는데, 원인이 한눈에 보이지 않는다.
-- 예약어가 아닌 이름으로 바꾼다.

ALTER TABLE batch_runs RENAME COLUMN trigger TO trigger_source;
