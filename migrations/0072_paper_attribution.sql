-- 모의 계좌 성과 분해 (docs/paper.md 10장, docs/infra.md 25.1450).
-- 체결일 종가 — 진입 이득(체결가가 그날 종가보다 얼마나 쌌나)을 따로 읽지 않고 잰다. 이전 체결은 비어 있다(알고 둔다)
ALTER TABLE paper_fills ADD COLUMN day_close REAL;
-- 날마다 계좌별 성과 분해(선정·비중 필터·진입·청산, 20 거래일 뒤 지수 대비) — 화면·주간 요약이 그대로 읽는다
ALTER TABLE paper_daily ADD COLUMN attribution_json TEXT;
