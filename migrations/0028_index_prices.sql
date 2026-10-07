-- 지수 일봉 (docs/signals.md 3.5 시장 추세 필터, docs/data-sources.md 4절).
--
-- 코스피·코스닥·S&P 500 종가만. 200 거래일 이동평균과 비교해 "약세 국면" 을 판정한다.
-- stocks/prices 에 가짜 종목으로 넣지 않는 이유: 유니버스·스코어·신호가 지수를 종목으로
-- 오인할 수 있고, 어느 질의가 지수를 걸러야 하는지 전부 기억해야 한다. 표를 따로 둔다.

CREATE TABLE IF NOT EXISTS index_prices (
    index_code  TEXT NOT NULL,   -- KOSPI · KOSDAQ · SP500 (batch/services/trend.py)
    date        TEXT NOT NULL,   -- 야후 일봉 날짜
    close       REAL NOT NULL,
    source      TEXT NOT NULL,   -- yfinance ^KS11 처럼 심볼까지
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (index_code, date)
);
