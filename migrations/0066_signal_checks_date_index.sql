-- 판정표 날짜 색인 — "마지막으로 계산한 날"(웹 `lib/recommend.LAST_CALC_DATE`·`PREVIOUS_AS_OF`, 배치 `db.last_signal_calc_date`)이
-- 날짜 내림차순으로 따라가 그 나라 첫 행에서 멈추게 (docs/infra.md 25.1204). 예전 질의는 그 나라 종목의 판정표 행을 전부 읽었다
-- (종목 × 3기간 × 날 수 — 추천 화면을 열 때마다)
CREATE INDEX IF NOT EXISTS idx_signal_checks_date ON signal_checks (as_of_date);
