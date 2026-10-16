-- 가상 G (목표 뒤 추적 매도) — B 와 매도 규칙 하나만 다르다 (docs/paper.md 1·2.2장, docs/infra.md 25.1481,
-- 2026-10-16 사용자 지시 "B의 규칙만을 따르지 말고 다른 좋은 아이디어 있으면 제시해봐").
-- sell_rule `trend`: 손절·기간초과는 B 와 같고, 목표도달에서 팔지 않고 목표에 한 번 닿은 뒤 고점 대비 손절폭만큼 빠지면 전량.
-- 매수는 B 와 같다(신호 상위 5 · 3회 분할 지정가). 입금 장부는 모든 계좌 공통이라 첫 배치에 지금까지의 입금 전액으로 시작한다
INSERT OR IGNORE INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n, selection)
VALUES ('G', '가상 G (목표 뒤 추적 매도)', 'trend', 1, '2026-10-16T00:00:00+00:00', 'tranche', 5, 'signal');
