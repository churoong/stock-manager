-- 사용자가 들고 있는 ETF 의 구성종목 비중 — 계좌 전체 노출(ETF 투시)용 (docs/portfolio.md 8장, docs/infra.md 25.1002).
--
-- 2026-10-07 사용자 지시("다 진행해", 차별점 9번). ETF 점수 작업(`etf_tilt`, 월 1회)이 이미 받는 보유 문서(N-PORT·KODEX·KIS 상위 30)
-- 가운데 **지금 보유(positions) 중인 ETF 것만** 우리 종목에 이어 남긴다. 우리 종목에 잇지 못한 비중은 남기지 않는다
-- (화면은 100 − 합을 "ETF 속 미확인" 으로 본다). 국내 상장 미국 지수 ETF 는 같은 지수의 미국 ETF 보유가 대리다(`basis` 에 적는다).
CREATE TABLE IF NOT EXISTS etf_lookthrough (
    etf_stock_id INTEGER NOT NULL REFERENCES stocks (id),  -- 보유 ETF (stocks.asset_type = 'etf')
    stock_id INTEGER NOT NULL REFERENCES stocks (id),      -- 구성종목
    weight_pct REAL NOT NULL,                              -- ETF 안 비중(%)
    as_of TEXT,                                            -- 보유 문서 기준일
    basis TEXT NOT NULL,                                   -- 어느 문서인가 (N-PORT 번호·KODEX·KIS, 대리면 대리 심볼)
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (etf_stock_id, stock_id)
);
