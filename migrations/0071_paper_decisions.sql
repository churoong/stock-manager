-- 모의 계좌 결정 원장 (docs/paper.md 9장, docs/infra.md 25.1449, 2026-10-15 사용자 지시 "노하우를 축적·발전시켜 실제 매매 프로그램으로").
--
-- 낸 주문만이 아니라 **하지 않은 결정**도 남긴다 — 2부에서 빠진 후보(사유), 현금·쿨다운으로 못 낸 회차, 계좌 규칙이 따르지 않은
-- 매도 플래그(B 가 들고 간 것), 지정가가 안 찬 회차. 그 뒤 5·20·60 거래일 성과와 같은 기간 지수를 배치가 채운다.
-- 안 한 결정의 결과를 모르면 규칙을 고쳤을 때 나아졌는지 잴 수 없다. 규칙 개선의 평가 자료다 — 지금 추천·점수에는 쓰지 않는다.
CREATE TABLE IF NOT EXISTS paper_decisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   TEXT NOT NULL REFERENCES paper_accounts (id),
    market       TEXT NOT NULL,            -- KR · US
    decided_on   TEXT NOT NULL,            -- 결정의 기준 거래일(그 시장 현지). 미체결은 안 찬 그 장날짜
    stock_id     INTEGER NOT NULL REFERENCES stocks (id),
    kind         TEXT NOT NULL CHECK (kind IN ('buy', 'skip', 'sell', 'hold_flag', 'missed')),
    reason       TEXT NOT NULL,            -- 2부 배분 · 제외 사유 · 플래그 사유 · 안 찬 까닭
    detail_json  TEXT,                     -- 그때의 기간·점수·비중·회차 가격·플래그 (작게)
    ref_close    REAL,                     -- 기준 종가 (종목 통화)
    ref_date     TEXT,                     -- 기준 종가의 날짜
    ret_5d       REAL,                     -- 기준 뒤 5·20·60 번째 거래일(그 종목 시세 행) 종가 수익률 %
    ret_20d      REAL,
    ret_60d      REAL,
    bench_5d     REAL,                     -- 같은 날짜까지 지수(국내 KOSPI · 미국 SP500) 수익률 %
    bench_20d    REAL,
    bench_60d    REAL,
    outcome_note TEXT,                     -- 시세가 끊겨 못 채운 까닭 등
    outcome_at   TEXT,                     -- 성과를 마지막으로 채운 시각
    created_at   TEXT NOT NULL,
    UNIQUE (account_id, market, decided_on, stock_id, kind)
);

-- 성과를 아직 다 못 채운 행만 — 날마다 이것만 훑는다
CREATE INDEX IF NOT EXISTS idx_paper_decisions_pending ON paper_decisions (decided_on) WHERE ret_60d IS NULL AND outcome_note IS NULL;
