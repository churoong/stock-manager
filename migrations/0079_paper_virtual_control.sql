-- 가상 대조 — 매수마다 같은 날·같은 시장 유니버스 무작위 20종목의 20 거래일 수익 안에서 그 매수의 분위 (docs/company.md 6장, docs/infra.md 25.1476).
-- 결정 원장의 성과가 찬 뒤 배치가 한 번 채운다. `control_n` 이 비어 있으면 아직 안 본 것, 0 이면 대조를 못 낸 것(그 까닭은 분위가 빈 채로)
ALTER TABLE paper_decisions ADD COLUMN control_pct REAL;    -- 0~100. 무작위로 골랐다면 평균 50
ALTER TABLE paper_decisions ADD COLUMN control_n INTEGER;   -- 20 거래일 성과를 낸 대조 종목 수
