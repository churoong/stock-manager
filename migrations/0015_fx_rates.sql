-- 환율 (docs/data-sources.md 4절 yfinance, docs/signals.md 3.4).
--
-- 총 투자가능금액은 설정에 원화로 하나만 있다. 미국 종목 권장 금액을 달러로 내려면 그날 환율이 필요하다.
-- 2026-09-17 전에는 원화 금액을 그대로 달러로 저장하는 버그가 있었다(docs/handoff.md 3.1-A A6).
-- 어느 환율로 나눴는지 근거표에서 확인할 수 있어야 하므로 날짜별로 남긴다.

CREATE TABLE IF NOT EXISTS fx_rates (
    pair        TEXT NOT NULL,   -- USDKRW = 1달러당 원
    date        TEXT NOT NULL,   -- 야후 일봉 날짜
    rate        REAL NOT NULL,   -- 그날 종가
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (pair, date)
);
