-- 장중 알림 (시장, 거래일) 색인 — 텔레그램 `/알림`(web/lib/telegramQa `alerts`)이 시장마다 마지막 거래일을 찾고 그날 알림만 읽게
-- (docs/infra.md 25.1347, 텔레그램 감사 #4). 알림 표는 지우지 않아 계속 자라는데, 예전 질의는 표 전체를 두 번 훑었다
CREATE INDEX IF NOT EXISTS idx_alerts_market_date ON alerts (market, trade_date);
