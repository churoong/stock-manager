-- KIS모의투자 주문 표를 날짜 범위로 읽는 질의 셋(웹 `kisPaper.KIS_ORDERS` 거래내역, 배치 `jobs/desk.KIS_ORDERS_SQL`·
-- `jobs/broker_mirror.COMPARE_SQL`)이 `idx_paper_broker_open (broker, status, trade_date)` 앞자리만 타 그 증권사 주문 전체를 훑었다(docs/infra.md 25.1637,
-- 웹 화면 감사 [필수] 4 — 1년치 하루 10건이면 결과 900행에 읽기 3,650행, /kis 를 열 때마다 국내·미국 두 번). 이 색인이면
-- `broker = ? AND trade_date >= ?` 범위만 읽는다(같은 재현 900행). 쓰기 비용은 주문 한 건에 색인 한 줄
CREATE INDEX IF NOT EXISTS idx_paper_broker_orders_trade ON paper_broker_orders (broker, trade_date);
