-- 미국 수정주가 재수집 대기열 (docs/adjust.md 8장).
--
-- 야후 Adj Close 는 배당·분할이 생기면 그 이전 날짜 전부가 다시 조정된다. 일일 배치는 10일치만
-- 받으므로 옛 행과 새 행의 조정 기준이 어긋난다. 전 종목 5년치를 매주 다시 받으면 한 주에 700만 행이라
-- Turso 쓰기 한도(월 1천만)를 태운다. 그래서 어긋난 종목만 찾아 여기에 적고, 주 1회 그 종목만 다시 받는다.
CREATE TABLE IF NOT EXISTS adjust_refresh_queue (
    stock_id      INTEGER PRIMARY KEY REFERENCES stocks (id),
    detected_at   TEXT NOT NULL,
    sample_date   TEXT NOT NULL,     -- 어긋남을 본 날짜
    ratio_before  REAL NOT NULL,     -- 저장돼 있던 adj_close / close
    ratio_after   REAL NOT NULL,     -- 새로 받은 adj_close / close
    done_at       TEXT               -- 다시 받은 시각. NULL 이면 대기 중
);
