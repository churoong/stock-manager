-- 국내 뉴스 매칭용 종목 별칭 (docs/infra.md 25.840, 2026-10-01 사용자 결정 — 사람이 관리하는 표).
--
-- 국내 뉴스는 기사 제목에서 거래소 약칭(`stocks.name_ko`)을 찾아 종목에 붙인다(web/lib/newsKr.ts `matchStocks`). 그런데 약칭과 기사 표기가 다른
-- 대형주(NAVER↔네이버, POSCO홀딩스↔포스코홀딩스, S-Oil↔에쓰오일, 신한지주↔신한금융 …)는 기사를 놓쳐 감성이 비거나 일부 기사로 치우쳤다.
-- 별칭도 약칭과 같은 규칙(3글자 이상, 단어 경계, 조사만 허용)으로 맞춘다. 두 글자 별칭은 넣지 않는다(오탐).
CREATE TABLE IF NOT EXISTS stock_aliases (
    stock_id   INTEGER NOT NULL REFERENCES stocks (id),
    alias      TEXT NOT NULL,
    added_by   TEXT NOT NULL,           -- claude(첫 목록) / user — 사람이 관리하는 표라 source·fetched_at 이 아니다(25.173)
    created_at TEXT NOT NULL,
    PRIMARY KEY (stock_id, alias)
);

-- 첫 목록 — 종목 코드로 붙인다(그 코드가 없으면 아무것도 넣지 않는다). 기사 표기는 연합뉴스 제목 관례 기준 [확인필요: 운영 name_ko 값]
-- D1 은 UNION 을 여럿 이은 SELECT 를 거절해(infra 25.5) VALUES 목록으로 붙인다
INSERT OR IGNORE INTO stock_aliases (stock_id, alias, added_by, created_at)
SELECT s.id, a.column2, 'claude', '2026-10-01T00:00:00+00:00'
FROM stocks s JOIN (VALUES
    ('035420', '네이버'),
    ('005490', '포스코홀딩스'),
    ('010950', '에쓰오일'),
    ('010950', 'S-OIL'),
    ('055550', '신한금융'),
    ('055550', '신한금융지주'),
    ('086790', '하나금융'),
    ('105560', 'KB금융지주'),
    ('316140', '우리금융'),
    ('138930', 'BNK금융'),
    ('175330', 'JB금융'),
    ('024110', 'IBK기업은행'),
    ('015760', '한국전력공사'),
    ('005380', '현대자동차'),
    ('017670', 'SKT'),
    ('096770', 'SK이노'),
    ('018260', '삼성SDS'),
    ('036570', 'NC소프트'),
    ('012450', '한화에어로'),
    ('009540', '한국조선해양'),
    ('329180', '현대중공업'),
    ('033780', '케이티앤지')
) a ON a.column1 = s.ticker
WHERE s.country = 'KR';
