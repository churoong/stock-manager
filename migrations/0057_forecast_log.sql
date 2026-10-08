-- 종목 분석의 예측 성적표와 가격 사다리 (docs/analysis.md 11·12장, docs/infra.md 25.1037·25.1038).
-- 2026-10-08 사용자: "추천순서대로 다 진행해" — 예측을 더 과감하게 하되 맞혔는지 쌓아 보인다.

-- 날마다 종목마다 한 행 — 그날 낸 예측을 **수익률로** 적는다(가격이 아니라). 평가할 때 기준일 종가를 지금 계열에서 다시 읽어
-- 수정주가가 다시 매겨져도 두 끝이 같은 잣대가 되게 한다.
--   models_json  {"capm": {"1": {"exp": 0.004, "lo68": -0.08, "hi68": 0.09, "lo90": ..., "hi90": ...}, ...},
--                 "consensus": {"12": {"exp": 0.35}}, ...}   — 모델 → 개월 → 수익률
CREATE TABLE IF NOT EXISTS forecast_log (
    as_of_date  TEXT NOT NULL,        -- 예측의 기준 종가 날짜
    stock_id    INTEGER NOT NULL REFERENCES stocks (id),
    country     TEXT NOT NULL,        -- KR · US (평가를 나라마다 한다)
    close       REAL NOT NULL,        -- 그날 기준 종가 (참고용 — 평가는 계열에서 다시 읽는다)
    models_json TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (as_of_date, stock_id)
);

-- 신호 판정표 기준을 **가격으로 푼 것** (가격 사다리). 기간 행마다 [{"label", "price", "need": "above"|"below"}].
-- 가격으로 풀 수 없는 기준(점수·재무)은 없다. 비어 있을 수 있다
ALTER TABLE signal_checks ADD COLUMN levels_json TEXT;
