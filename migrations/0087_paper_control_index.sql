-- 가상 대조 대기(docs/company.md 6장, docs/infra.md 25.1571) — `jobs/paper.PENDING_CONTROL_SQL` 이 성과가 찼는데 대조를 안 본 매수를
-- 찾느라 결정 원장 전체를 훑고 임시 정렬을 했다(운용 감사 [권고] 5 — 1년 차면 하루 수십만 행). 대기 행만 담는 부분 색인을 둔다.
-- 쓰기 비용: 대조를 보면(control_n 이 차면) 색인에서 빠지므로 색인은 늘 작다
CREATE INDEX IF NOT EXISTS idx_paper_decisions_control ON paper_decisions (market, decided_on)
    WHERE kind = 'buy' AND ret_20d IS NOT NULL AND control_n IS NULL;
