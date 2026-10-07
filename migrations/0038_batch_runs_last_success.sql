-- 무응답 감시가 매시간 묻는 "작업마다 마지막으로 돌았는가" 를 싸게 답하기 위한 인덱스
-- (docs/infra.md 24.4). 예전 질의는 행마다 batch_runs 를 다시 훑어 기록이 쌓일수록
-- 읽는 행이 제곱으로 늘었다. 질의를 한 번 훑는 형태로 바꾸고, 이 인덱스로 그 한 번도 줄인다.
CREATE INDEX IF NOT EXISTS idx_batch_runs_last_success
    ON batch_runs (job_name, status, finished_at DESC);
