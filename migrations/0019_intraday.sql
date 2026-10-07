-- 관심 종목 · 장중 감시 대상 · 거래 세션 · 알림 (docs/intraday.md, Step 14).
--
-- 장중 경로(web/app/api/cron/intraday)는 **읽고 alerts 만 쓴다.** 점수·신호·매매를 바꾸지 않는다(CLAUDE.md).
-- 감시 대상과 거래 세션은 일일 배치가 미리 만들어 둔다(design.md 2.8 "목록은 배치 종료 시 확정해 캐시한다").

CREATE TABLE IF NOT EXISTS watchlist (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id          INTEGER NOT NULL UNIQUE REFERENCES stocks (id),
    added_at          TEXT NOT NULL,
    memo              TEXT,
    target_buy_price  REAL,                 -- 종목 통화. 이 값 이하로 내려오면 알림 (비우면 급등락·거래량만)
    alert_enabled     INTEGER NOT NULL DEFAULT 1
);

-- 거래소 정규장 시각. 배치가 exchange_calendars 로 앞으로 2주치를 채운다. 웹에는 휴장일 계산기가 없어서다
CREATE TABLE IF NOT EXISTS market_sessions (
    market        TEXT NOT NULL,            -- KR / US
    date          TEXT NOT NULL,            -- 현지 날짜
    open_utc      TEXT NOT NULL,
    close_utc     TEXT NOT NULL,
    source        TEXT NOT NULL,            -- exchange_calendars 버전
    PRIMARY KEY (market, date)
);

-- 그날 감시할 종목과 문턱. 보유 · 당일 추천(신호) 을 합친다. 관심 종목은 장중 경로가 watchlist 에서 바로 읽는다
CREATE TABLE IF NOT EXISTS monitor_targets (
    market            TEXT NOT NULL,
    stock_id          INTEGER NOT NULL REFERENCES stocks (id),
    yahoo_symbol      TEXT NOT NULL,
    name              TEXT NOT NULL,
    currency          TEXT NOT NULL,
    reasons           TEXT NOT NULL,        -- JSON ["holding", "signal:mid"]
    buy_zone_low      REAL,                 -- 신호의 권장 매수 구간
    buy_zone_high     REAL,
    target_price      REAL,                 -- 보유: 평균단가 × (1 + 목표%) / 신호만: 신호의 목표가
    stop_price        REAL,
    prev_close        REAL,                 -- DB 마지막 종가 (야후가 전일 종가를 안 주면 대신)
    avg_volume_20d    REAL,
    dart_corp_code    TEXT,                 -- 보유 국내 종목의 공시 확인용
    built_at          TEXT NOT NULL,
    PRIMARY KEY (market, stock_id)
);

-- 알림. (종목, 트리거, 현지 날짜) 유니크 → 같은 종목·같은 트리거는 하루 한 번 (DB 가 보장)
CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id      INTEGER NOT NULL REFERENCES stocks (id),
    market        TEXT NOT NULL,
    trade_date    TEXT NOT NULL,            -- 시장 현지 날짜
    trigger_type  TEXT NOT NULL,            -- buy_zone / target / stop / spike_up / spike_down / volume / disclosure
    message       TEXT NOT NULL,
    data          TEXT NOT NULL,            -- JSON: 판정에 쓴 시세·문턱·시세 시각
    created_at    TEXT NOT NULL,
    sent_at       TEXT,                     -- 텔레그램 발송 시각. 조용시간이면 비어 있다가 해제 뒤 묶어 보낸다
    UNIQUE (stock_id, trigger_type, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_alerts_pending ON alerts (sent_at, created_at);
