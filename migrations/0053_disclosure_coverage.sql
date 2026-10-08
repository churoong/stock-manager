-- 시장 전체 공시를 "그날 다 받았나" 의 기록 (docs/disclosure_reaction.md 2장, docs/infra.md 25.1014).
--
-- 14회차 발굴·교차검증(2026-10-08): 시장 전체 공시를 1년치 모았는데도 자사주(`buyback`)·희석(`dilution`) IC 가 판정 불가였다 —
-- 백테스트는 `disclosures_kr --all` 의 `covered_ids` 만 "덮은 구간" 으로 셌다. 시장 전체 수집이 **종류·날짜마다** 오류·잘림 없이
-- 받은 날을 여기에 남기면 그 구간은 국내 전 종목을 덮은 것이다(누락이 0 이 아니라 NULL 이 되게, 25.484).
-- 실행 끝이 아니라 **하루치를 받을 때마다** 쓴다 — 60분 제한에 끊겨도 받은 날까지는 남는다.
CREATE TABLE IF NOT EXISTS disclosure_coverage (
    kind TEXT NOT NULL,          -- B 주요사항보고 · I 거래소공시
    day TEXT NOT NULL,           -- 접수일 YYYY-MM-DD (주말도 부른다 — 구간이 끊기지 않게)
    rows INTEGER NOT NULL,       -- 그날 받은 상장사 공시 수
    calls INTEGER NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (kind, day)
);
