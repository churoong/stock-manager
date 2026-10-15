-- 모의 투자사 헌장 적용 — 규칙서 판 2 (docs/company.md 1·4장, docs/infra.md 25.1468).
--
-- A 가치 · C 모멘텀 · D 장기보유 · E 안정 에 헌장을 적는다. B(기준)·R1·R2(무작위 대조)는 비운 채(판 1).
-- 값은 `batch/services/company.CHARTERS` 를 그대로 옮긴 것이다 — 시험(`test_charter_migration_matches_code`)이 대조한다.
-- 소급 재생(25.1464·25.1467)으로 문턱을 거른 뒤의 값. 모의 주문이 아직 0건인 날(2026-10-15) 넣어 **첫 거래일(10-16)부터 판 2** 로
-- 시작한다 — 판 1 로 산 "물려받은 보유" 가 생기지 않는다(검증 [필수] 7 이 걱정한 전환 문제를 피한다).
-- 매도 원칙(`value`·`momentum`)은 25.1462 에서 코드에 먼저 넣었다.
UPDATE paper_accounts SET charter_json = '{"agenda": 20, "lead": "research", "max_mdd": null, "name": "가치투자사", "philosophy": "싸고 튼튼한 회사를 산다. 손실이 커지거나 회사가 나빠지면 판다", "research_mix": {"growth": 0.1, "quality": 0.4, "value": 0.5}, "sell_rule": "value", "threshold": 1.0, "top_n": 5, "veto": ["governance"], "weights": {"broker": 1, "flows": 0.5, "governance": 1, "macro": 0.5, "quant": 0.5, "research": 2, "risk": 1, "sentiment": 0.5}}', charter_since = '2026-10-16', rule_version = 2,
    sell_rule = 'value', name = '모의 A (가치투자사)' WHERE id = 'A';
UPDATE paper_accounts SET charter_json = '{"agenda": 20, "lead": "quant", "max_mdd": null, "name": "모멘텀투자사", "philosophy": "오르는 것을 산다. 목표에서 팔지 않고, 손실·악재에는 바로 나온다", "research_mix": {"growth": 0.5, "quality": 0.3, "value": 0.2}, "sell_rule": "momentum", "threshold": 1.0, "top_n": 5, "veto": [], "weights": {"broker": 1, "flows": 1.5, "governance": 0.5, "macro": 1.5, "quant": 2, "research": 0.5, "risk": 0.5, "sentiment": 1}}', charter_since = '2026-10-16', rule_version = 2,
    sell_rule = 'momentum', name = '모의 C (모멘텀투자사)' WHERE id = 'C';
UPDATE paper_accounts SET charter_json = '{"agenda": 20, "lead": "research", "max_mdd": null, "name": "장기보유투자사", "philosophy": "좋은 회사를 사서 기다린다. 흔들림에 팔지 않는다", "research_mix": {"growth": 0.3, "quality": 0.5, "value": 0.2}, "sell_rule": "time", "threshold": 1.0, "top_n": 5, "veto": ["governance"], "weights": {"broker": 1, "flows": 0.5, "governance": 1, "macro": 0, "quant": 0.5, "research": 2, "risk": 1, "sentiment": 0}}', charter_since = '2026-10-16', rule_version = 2,
    sell_rule = 'time', name = '모의 D (장기보유투자사)' WHERE id = 'D';
UPDATE paper_accounts SET charter_json = '{"agenda": 30, "lead": "risk", "max_mdd": -0.6, "name": "안정투자사", "philosophy": "잃지 않는 것이 먼저다. 넓게 나누고, 위험한 회사·약세장에서는 새로 덜 산다", "research_mix": {"growth": 0.2, "quality": 0.5, "value": 0.3}, "sell_rule": "core", "threshold": 0.8, "top_n": 10, "veto": ["governance", "risk"], "weights": {"broker": 0.5, "flows": 0.5, "governance": 1, "macro": 1.5, "quant": 0.5, "research": 1, "risk": 2, "sentiment": 0.5}}', charter_since = '2026-10-16', rule_version = 2,
    sell_rule = 'core', name = '모의 E (안정투자사)' WHERE id = 'E';
