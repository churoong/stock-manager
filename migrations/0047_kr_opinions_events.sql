-- 증권사 투자의견 · 예탁원 기업행위 일정 (docs/data-sources.md 3.1, docs/infra.md 25.988). 2026-10-07 사용자 지시("모두 진행해줘").
--
-- 투자의견은 종목마다 1년에 100행까지 준다(실측) — 발표일(`date`) 기준이라 시점 그대로 백테스트할 수 있다. 지금은 모으기만 한다
-- (팩터로 쓰려면 docs/factors.md 에 식을 먼저).
-- 기업행위 일정은 **앞날**을 준다 — 배당·무상증자·유상증자·액면교체(분할·병합·합병). 기업행위를 가격이 튄 **뒤에** 추정하던 것(수정주가
-- 25.138, 첫날 가드 25.597)을 **미리** 알리는 데 쓴다.
CREATE TABLE IF NOT EXISTS kr_opinions (
    stock_id INTEGER NOT NULL REFERENCES stocks (id),
    date TEXT NOT NULL,                 -- 발표일
    broker TEXT NOT NULL,               -- 증권사 (mbcr_name)
    opinion TEXT,                       -- 원문 (BUY · 매수 · 중립 …)
    opinion_code INTEGER,               -- invt_opnn_cls_code (뜻 [확인필요] — 원문을 함께 둔다)
    prev_opinion_code INTEGER,
    target_price REAL,                  -- 목표가(원). 0 이면 NULL
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (stock_id, date, broker)
);

CREATE TABLE IF NOT EXISTS kr_corp_events (
    code TEXT NOT NULL,                 -- 단축코드 (우리 종목이 아닐 수도 있다)
    kind TEXT NOT NULL,                 -- dividend · bonus · rights · split (액면교체·합병·분할 포함)
    record_date TEXT NOT NULL,          -- 기준일
    name TEXT,
    detail TEXT,                        -- 원 응답 한 행(JSON)
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (code, kind, record_date)
);
