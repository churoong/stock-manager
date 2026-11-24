-- 거장 단독 규칙 계좌 Z3(그린블랫)·Z4(오닐) — docs/company.md 12.5(사전 등록)·12.7(판정), docs/paper.md 18장, docs/infra.md 25.2319.
-- 2026-11-23 사용자 지시 "세계 유명한 투자가(예를들어, 워렌버핏, 피터린치 등)의 투자기법을 녹인 모의투자계좌" → "계좌 이름이나
-- 에이전트(부서명)에 해당 이름도 같이 넣어줘". 헌장 없는 규칙 계좌 — 고르기 `magic_formula`·`canslim`(`jobs/paper_solo`),
-- 매도 `hold_1y`·`canslim_exit`(`jobs/paper._단독넘음`). 엔진(`pp.SELECTIONS`·`pp.SELL_RULES`)이 같은 커밋에 들어간다 —
-- 행이 엔진보다 먼저 들어오면 그 계좌만 건너뛴다(25.1871·25.2317).
-- top_n 은 시장당 보유 상한(Z3 12 · Z4 5)을 적어 둔다(엔진은 `services/solo` 상수를 쓴다 — 시험이 대조). 진입 다음 장 시가.
-- id 는 Y 뒤·ZL 앞으로 정렬된다(계좌 루프에서 ZL·ZM 앞 — 무변화 시험이 ZL·ZM 을 품는다). 따로 재는 실험 계좌(`SEPARATE_IDS`).
-- 개설일 11-26 — 그 전 실행은 개설일 막이(`OPEN_GUARD_FROM`)가 평가·매매를 막는다. 사전 등록 desk.PREREG (Z3,1)·(Z4,1)
INSERT OR IGNORE INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n, selection)
VALUES ('Z3', '가상 그린블랫 계좌 (마법공식)', 'hold_1y', 1, '2026-11-26T00:00:00+00:00', 'open', 12, 'magic_formula'),
       ('Z4', '가상 오닐 계좌 (CAN SLIM)', 'canslim_exit', 1, '2026-11-26T00:00:00+00:00', 'open', 5, 'canslim');
