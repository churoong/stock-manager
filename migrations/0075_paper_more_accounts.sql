-- 모의 계좌 7개 (docs/paper.md 1장, docs/infra.md 25.1458, 2026-10-15 사용자 지시 "두개의 모의투자자로는 부족할거 같다. 모수가 너무 적어").
--
-- 계좌의 규칙을 칸으로 적는다 — 코드가 계좌 이름을 보지 않고 칸만 읽는다. A·B 는 기본값 그대로(지금과 같은 규칙).
--   entry_rule : tranche (3회 분할 지정가, 2.1) · open (배분 금액 전부를 다음 장 시가 시장가 한 번, 2.1.1)
--   top_n      : 점수 상위 몇 종목에서 고르나 (2부와 같은 select_top 의 n)
--   selection  : signal (신호 종목) · random (같은 자리 수·비중으로 유니버스에서 무작위, 2.1.2)
ALTER TABLE paper_accounts ADD COLUMN entry_rule TEXT NOT NULL DEFAULT 'tranche';
ALTER TABLE paper_accounts ADD COLUMN top_n INTEGER NOT NULL DEFAULT 5;
ALTER TABLE paper_accounts ADD COLUMN selection TEXT NOT NULL DEFAULT 'signal';

-- 시장가 매수의 기준가(신호의 마지막 종가, 종목 통화) — 값을 모르는 시장가 주문의 예약 잣대 (2.1.1). 지정가·매도는 비움
ALTER TABLE paper_orders ADD COLUMN ref_price REAL;

-- sell_rule 에 time (기간초과 하나) 이 더해진다 — 0070 의 칸 설명은 all · core
INSERT OR IGNORE INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n, selection)
VALUES ('C', '모의 C (시가 일괄 매수)', 'core', 1, '2026-10-15T00:00:00+00:00', 'open', 5, 'signal'),
       ('D', '모의 D (기간 보유)', 'time', 1, '2026-10-15T00:00:00+00:00', 'tranche', 5, 'signal'),
       ('E', '모의 E (상위 10)', 'core', 1, '2026-10-15T00:00:00+00:00', 'tranche', 10, 'signal'),
       ('R1', '모의 R1 (무작위 대조)', 'core', 1, '2026-10-15T00:00:00+00:00', 'open', 5, 'random'),
       ('R2', '모의 R2 (무작위 대조)', 'core', 1, '2026-10-15T00:00:00+00:00', 'open', 5, 'random');
