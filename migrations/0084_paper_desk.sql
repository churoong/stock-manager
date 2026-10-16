-- 운용 상황실 요약 — 웹 첫 화면(운용)이 읽는다 (docs/desk.md 2장, docs/infra.md 25.1501).
-- 배치가 실행마다 그날(한국 날짜) 한 줄에 합쳐 적는다 — 국내 배치가 markets.KR, 미국 배치가 markets.US. 계좌 순위·KIS 계좌는
-- 마지막 실행 것으로 덮는다(확인할 일은 시장 칸마다). 웹은 계산하지 않는다 — 순위·공통 구간·R 대비는 여기 적힌 값 그대로 보인다
CREATE TABLE IF NOT EXISTS paper_desk (
    as_of        TEXT PRIMARY KEY,               -- 한국 날짜
    summary_json TEXT NOT NULL,                  -- {leaderboard, kis_account, markets: {KR: {..., checks}, US: {...}}}
    updated_at   TEXT NOT NULL                   -- 마지막으로 고쳐 쓴 시각(UTC). 배치가 만든 파생이라 source 열은 없다
);
