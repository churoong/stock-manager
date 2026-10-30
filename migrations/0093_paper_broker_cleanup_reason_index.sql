-- KIS모의투자 — 내부 주문 없는 줄의 "같은 날 같은 종목 한 번" 을 **사유 갈래마다** 로 (docs/infra.md 25.2010, 25.1772).
--
-- 0078 의 부분 유일 색인 `idx_paper_broker_cleanup (broker, trade_date, stock_id, side) WHERE paper_order_id IS NULL` 은
-- 아침 베끼기의 정리 매도(`정리`)와 장중 손절·익절(`장중손절`·`장중익절`)을 한 칸으로 묶었다. 그래서 아침에 그 종목을
-- 정리 매도한 날은 장중 감시가 손절선에 닿아도 줄을 못 적어 KIS 에 손절을 내지 못했다 — 2026-10-30 실제로 났다
-- (텔레그램 "정리 매도 줄과 겹쳐 장중 손절·익절을 내지 못함", 장중 현재가가 손절선 아래).
--
-- 새 색인은 갈래를 `reason = '정리'` 참·거짓 둘로 나눈다:
--   · 정리 줄은 예전처럼 종목·날마다 하나(같은 날 다시 도는 아침 실행이 두 번 정리하지 않게)
--   · 장중 줄(손절·익절 어느 쪽이든)도 종목·날마다 하나(감시 실행이 겹쳐도 두 번 팔지 않게 — 예전과 같다)
--   · 둘은 서로 막지 않는다. 함께 팔아도 장중 매도 수량은 KIS 잔고의 **주문 가능 수량**(정리 주문이 잡은 몫을 뺀 값)을
--     넘지 않는다(`intraday_guard.hits` — `min(sellable, H 수량)`)
-- 지금 운영 행은 종목·날마다 내부 주문 없는 줄이 많아야 하나라 더 잘게 나눈 새 색인과 부딪히지 않는다.
DROP INDEX IF EXISTS idx_paper_broker_cleanup;
CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_broker_cleanup_kind
    ON paper_broker_orders (broker, trade_date, stock_id, side, reason = '정리') WHERE paper_order_id IS NULL;
