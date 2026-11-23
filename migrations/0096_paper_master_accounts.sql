-- 거장 계좌 ZM(거장 위원회)·ZL(린치) — docs/company.md 12장, docs/infra.md 25.2267.
-- 2026-11-23 사용자 지시 "세계 유명한 투자가(예를들어, 워렌버핏, 피터린치 등)의 투자기법을 녹인 모의투자계좌" → "유명 투자가들을
-- 에이전트로 활동해서 회의를 통해 통과된 종목을 매매하는 계좌" → "계좌 이름이나 에이전트(부서명)에 해당 이름도 같이 넣어줘".
-- 헌장(charter_json)은 `batch/services/masters.CHARTERS` 그대로(12.4 사전 등록 값, 시험이 대조). 판 1, 매도 core · 분할 지정가.
-- 12.6 수익 봉인 거름(25.2266): ZM·ZL 통과 — ZN(그레이엄 계좌)은 미국 중복 1.00 으로 열지 않는다(행 없음).
-- id 는 Y 뒤로 정렬된다(계좌 루프 맨 끝 — 앞 계좌가 보는 값이 바뀌지 않는다, 무변화 시험). 따로 재는 실험 계좌(`SEPARATE_IDS`).
-- 개설일 11-24 — 그 전 실행은 개설일 막이(`OPEN_GUARD_FROM`)가 평가·매매를 막는다. 사전 등록 desk.PREREG (ZM,1)·(ZL,1)
INSERT OR IGNORE INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n, selection, charter_json, charter_since)
VALUES ('ZM', '가상 거장 위원회 (버핏·린치·그레이엄·그린블랫·오닐)', 'core', 1, '2026-11-24T00:00:00+00:00', 'tranche', 5, 'signal',
        '{"agenda": 30, "lead": "buffett", "max_mdd": -0.6, "name": "거장 위원회", "philosophy": "버핏·린치·그레이엄·그린블랫·오닐의 책 규칙이 함께 회의해 통과한 종목을 산다", "research_mix": null, "sell_rule": "core", "threshold": 1.0, "top_n": 5, "variant": "masters_committee_v1", "veto": ["governance", "risk"], "weights": {"buffett": 1, "governance": 1, "graham": 1, "greenblatt": 1, "lynch": 1, "oneil_earn": 0.5, "oneil_price": 0.5, "risk": 0.5}}', '2026-11-24'),
       ('ZL', '가상 린치 계좌', 'core', 1, '2026-11-24T00:00:00+00:00', 'tranche', 5, 'signal',
        '{"agenda": 50, "lead": "lynch", "max_mdd": null, "name": "린치 계좌", "philosophy": "성장 대비 싸고(린치 비율 ≥ 2) 재무가 튼튼한 종목을 산다", "research_mix": null, "sell_rule": "core", "threshold": 2.0, "top_n": 5, "variant": "lynch_halves_v1", "veto": ["governance"], "weights": {"governance": 1, "lynch": 2, "lynch_check": 2}}', '2026-11-24');
