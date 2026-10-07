-- 공시 반응 통계 (docs/disclosure_reaction.md, docs/infra.md 25.996). 2026-10-07 사용자 지시("다 진행해" — 차별점 2번).
-- 시장 전체 주요 공시(DART 주요사항보고·거래소공시)를 `disclosures` 에 모으고, 유형마다 5거래일 초과수익을 매주 다시 계산한다.
-- 계산 결과라 언제든 다시 만들 수 있다. `keywords` 는 웹이 같은 규칙으로 공시 제목을 가르도록 함께 둔다(규칙의 단일 정의처는
-- batch/services/disclosure_reaction.TYPES).
CREATE TABLE IF NOT EXISTS disclosure_reaction (
    type TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    keywords TEXT NOT NULL,        -- JSON 배열
    priority INTEGER NOT NULL,     -- 위에서부터 처음 맞는 것 (작을수록 먼저)
    n INTEGER NOT NULL,
    mean_pct REAL,
    median_pct REAL,
    pos_pct REAL,
    window_days INTEGER NOT NULL,
    since TEXT,                    -- 표본의 첫 공시일
    as_of TEXT,                    -- 시세 기준일
    computed_at TEXT NOT NULL
);
