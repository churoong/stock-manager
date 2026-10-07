-- 국내 수급 일별 — 투자자별 순매수 · 공매도 · 신용잔고 (docs/data-sources.md 3.1, docs/infra.md 25.987).
--
-- 2026-10-07 사용자 지시("모두 진행해줘") — KIS 로 받는다. 투자자별·신용은 **최근 30일만** 주고(실측) 공매도는 약 100일이라,
-- 과거로 거슬러 받을 수 없다 → **오늘부터 쌓아야** 나중에 팩터로 백테스트할 수 있다(문서 없는 팩터 금지 — 지금은 모으기만).
-- 세 출처가 같은 (종목, 날짜) 행의 다른 칸을 채운다. 비어 있으면 그 출처를 못 받은 것이다(0 이 아니다).
CREATE TABLE IF NOT EXISTS kr_flows (
    stock_id INTEGER NOT NULL REFERENCES stocks (id),
    date TEXT NOT NULL,                 -- 매매일 YYYY-MM-DD
    frgn_net_qty INTEGER,               -- 외국인 순매수 수량(주)
    orgn_net_qty INTEGER,               -- 기관 순매수 수량
    prsn_net_qty INTEGER,               -- 개인 순매수 수량
    frgn_net_amt INTEGER,               -- 외국인 순매수 대금(백만원)
    orgn_net_amt INTEGER,
    prsn_net_amt INTEGER,
    short_qty INTEGER,                  -- 공매도 체결 수량
    short_vol_pct REAL,                 -- 공매도 수량 ÷ 거래량 (%)
    credit_rmnd_qty INTEGER,            -- 신용 융자 잔고 수량
    credit_rmnd_pct REAL,               -- 신용 잔고율 (% of 상장주식)
    source TEXT NOT NULL,               -- kis_openapi
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (stock_id, date)
);
