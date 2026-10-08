-- 날마다 통째로 훑던 두 질의에 색인 (docs/infra.md 25.1036, 사용자 "turso 사용량 다른 데서 더 줄일 거 있으면 줄여줘").
--   batch_runs (finished_at)  시세 사본 맞추기(`price_replica.touched_ranges`)가 "그 뒤로 끝난 실행" 을 찾는다 — 평일 하루 두세 번,
--                             실행 기록 전체(step_log 포함)를 훑었다
--   kr_opinions (date)        종목 분석 의견(`verdicts` 증권사 목표가, 지난 90일)이 국내 일일 배치마다 의견 표 전체를 훑었다
CREATE INDEX IF NOT EXISTS idx_batch_runs_finished ON batch_runs (finished_at);
CREATE INDEX IF NOT EXISTS idx_kr_opinions_date ON kr_opinions (date);
