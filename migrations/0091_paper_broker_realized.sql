-- KIS모의투자 매도의 실현손익·환차 몫 (docs/paper.md 15장, docs/infra.md 25.1694, 2026-10-22 사용자 지시 "모의투자에 실현손익이나
-- 기간실현손익을 보는게 없네? 이것도 당연히 있어야지"). **우리 주문 기록**(체결 수량·평균 체결가)의 평균 원가 — KIS 가 매긴
-- 수수료·세금은 기록에 없어 빠진다 [확인필요]. 미국은 체결일 USDKRW 로 원화, 환차 몫 = 현지 원가 × (매도 환율 − 매수 평균 환율).
-- 일일 배치의 KIS 단계가 맞추기 뒤 날마다 다시 센다(바뀐 줄만 적음). 웹은 계산하지 않는다
ALTER TABLE paper_broker_orders ADD COLUMN realized_krw REAL;
ALTER TABLE paper_broker_orders ADD COLUMN realized_fx_krw REAL;
