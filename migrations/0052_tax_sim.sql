-- 계좌별 세후 적립 시뮬레이션 결과 (docs/etf.md 11.7, docs/infra.md 25.1003). 2026-10-07 사용자 지시("다 진행해", 차별점 6번).
-- 포트폴리오 재계산이 설정 세율(`taxes`)로 만든다 — 세율을 저장하면 재계산이 깨어나므로 늘 지금 설정과 맞는다.
-- 웹은 계산하지 않고 읽기만 한다(CLAUDE.md).
ALTER TABLE portfolio_summary ADD COLUMN tax_sim_json TEXT;
