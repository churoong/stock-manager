-- 분할 매수 계획 진행 추적 (docs/intraday.md 2.1, docs/infra.md 25.999). 2026-10-07 사용자 지시("다 진행해" — 차별점 5번).
-- 보유 종목에 최근 신호의 3회 분할 계획이 있으면, 신호 뒤 매수 기록 수로 몇 차까지 샀는지 세어 **다음 차수 가격**을 감시 목록에 싣는다.
-- 장중 감시가 그 가격에 닿으면 "분할 매수 2차 가격 도달 (계획 2/3)" 알림을 낸다. 감시 목록은 매일 다시 만든다.
ALTER TABLE monitor_targets ADD COLUMN next_tranche_price REAL;
ALTER TABLE monitor_targets ADD COLUMN next_tranche_step INTEGER;
ALTER TABLE monitor_targets ADD COLUMN tranche_plan_date TEXT;   -- 계획을 낸 신호의 기준일
