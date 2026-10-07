-- 종목 찾기 조건 저장 (설계서 3.5 screener_presets, docs/screener.md). 사용자 입력이라 배치가 건드리지 않는다.
-- 조건은 JSON 그대로 둔다. 화면의 조건 항목이 늘어도 표를 바꾸지 않고, 읽을 때 스키마로 다시 검사한다.
CREATE TABLE IF NOT EXISTS screener_presets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    country       TEXT NOT NULL,    -- KR · US. 단위(억/$M)와 조건이 나라마다 달라 섞지 않는다
    filters_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT,
    UNIQUE (country, name)
);
