-- 가상 X(국내ETF)·Y(미국ETF) — ETF 전술 계좌 (docs/paper.md 17장, docs/infra.md 25.2166).
-- 2026-11-06 사용자 지시 "지수 예측을 통한 대표지수ETF(ex. kodex 200, kodex 200 인버스) 를 매수매도하는 ETF 계좌도 하나 추가해줘.
-- 이 가상계좌는 ETF만 매수매도를 하는거고, 지수, 테마 등을 예측해서 ETF에 투자하는 방식이야" → "계좌는 두개를 만들거야. 국내ETF, 미국ETF".
-- 새 칸 없이 칸 **값**만: 엔진(jobs/paper_etf)·상황실·웹 코드가 같은 커밋에 들어간다.
--   X: KOSPI 국면(200일선·20거래일 수익)으로 KODEX 200 98% · KODEX 인버스 50% · 현금 (etf_tactical_v1)
--   Y: S&P 500 국면으로 SPY 98% · SH 50% · 현금 (같은 규칙)
-- 국면이 바뀐 날만 갈아탄다 — 오늘 팔고 다음 실행에 산다. 판단은 각자 시장 실행에서만(X 국내, Y 미국).
-- id 는 T 뒤로 정렬되게(계좌 루프 맨 끝 — 앞 계좌가 보는 값이 바뀌지 않는다, 무변화 시험).
-- 둘 다 따로 재는 실험 계좌(`SEPARATE_IDS`) — 순위표 공통 구간·챔피언 후보·KIS 베끼기 밖. 사전 등록 desk.PREREG (X,1)·(Y,1)
-- 개설일 11-09(월) — 그 전 실행은 개설일 막이(`OPEN_GUARD_FROM`)가 거래를 막는다.
INSERT OR IGNORE INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n, selection)
VALUES ('X', '가상 X (국내ETF — KOSPI 국면, KODEX 200·인버스·현금)', 'etf_switch', 1, '2026-11-09T00:00:00+00:00', 'open', 1, 'etf_tactical'),
       ('Y', '가상 Y (미국ETF — S&P 500 국면, SPY·SH·현금)', 'etf_switch', 1, '2026-11-09T00:00:00+00:00', 'open', 1, 'etf_tactical');
