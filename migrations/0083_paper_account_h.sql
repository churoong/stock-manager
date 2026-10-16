-- 가상 H — 지주사(성적 가중 합의) (docs/company.md 10장, docs/infra.md 25.1482, 2026-10-16 사용자 제안·결정).
-- G 와 매수 선정 하나만 다르다: 매도 `trend`(G 와 같음), 선정 `weighted` — A·C·D·E 위원회 가결과 B 점수 상위 5 를 추천으로 받아
-- 회사 성적 가중치 × 세기의 합으로 점수, 두 곳 이상 추천한 종목만, 상위 5.
-- charter_json 은 위원회 헌장이 아니라 **투표 회사 헌장 지문**이다 — 지금 지문이 다르면 H 는 그날 사지 않는다(투표 회사가 판을 올리면 H 도 판을 올린다).
-- 지문은 0077 헌장(services/company.CHARTERS)의 `charter_hash` (tests/test_holding_1482.py 가 대조)
INSERT OR IGNORE INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n, selection, charter_json)
VALUES ('H', '가상 H (지주사 — 성적 가중 합의)', 'trend', 1, '2026-10-16T00:00:00+00:00', 'tranche', 5, 'weighted',
        '{"kind": "holding", "voters": {"A": "96b935fc68a5", "C": "ad7fe5ee8e67", "D": "ad781244b5e9", "E": "27661de23162"}, "b_top_n": 5}');
