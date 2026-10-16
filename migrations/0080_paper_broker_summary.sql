-- KIS모의투자 비교 요약 — 배치가 날마다 지난 28일의 체결 일치율·가격 차이·거부 까닭을 내 그날 잔고 줄에 싣는다
-- (docs/paper.md 15.3, docs/infra.md 25.1478). 웹·주간 요약은 이것을 읽기만 한다(웹은 계산하지 않는다)
ALTER TABLE paper_broker_daily ADD COLUMN summary_json TEXT;
