-- 종목 여정 (docs/desk.md 5장, docs/infra.md 25.1506) — 연구실이 한 종목의 추천 → 결정 → 주문 → 가상 체결 → KIS 주문을 시간순으로 본다.
-- 세 표 모두 종목으로 찾을 색인이 없어 종목 하나를 볼 때마다 표 전체를 훑었다(결정 원장은 1년 수만~수십만 행). 종목·날짜 순 색인을 둔다.
-- 쓰기 비용: 행마다 색인 하나씩 더 — 결정 원장이 하루 수백 행이라 작다
CREATE INDEX IF NOT EXISTS idx_paper_decisions_stock ON paper_decisions (stock_id, decided_on);
CREATE INDEX IF NOT EXISTS idx_paper_orders_stock ON paper_orders (stock_id, decided_on);
CREATE INDEX IF NOT EXISTS idx_paper_broker_orders_stock ON paper_broker_orders (stock_id, trade_date);
