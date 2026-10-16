-- 판 이력 (docs/desk.md 3장, docs/infra.md 25.1505) — 계좌마다 규칙서 판을 **처음 본 결정일**.
-- 25.1502 는 판의 시작을 그 판의 첫 주문으로 잡아, 판을 올린 뒤 며칠 사지도 팔지도 않으면 그 기간이 앞 판에 붙었다(검토 2).
-- 일일 배치(운용 요약 단계)가 실행마다 계좌의 지금 판을 보고, 처음 보는 (계좌, 판)이면 그 실행의 판정일을 적는다.
-- 처음 적는 계좌(이 표가 생기기 전부터 있던 계좌·새 계좌)는 since = '' — 계좌의 처음부터.
-- 판은 마이그레이션이 올리고 배치가 그 실행에서 바로 보므로, 배치가 빠진 날이 없으면 since 는 새 판의 첫 결정일이다
CREATE TABLE IF NOT EXISTS paper_rule_versions (
    account_id   TEXT NOT NULL REFERENCES paper_accounts (id),
    rule_version INTEGER NOT NULL,
    since        TEXT NOT NULL,          -- 이 판으로 처음 결정한 판정일(그 시장 현지). '' = 계좌의 처음부터
    noted_at     TEXT NOT NULL,          -- 배치가 적은 시각(UTC)
    PRIMARY KEY (account_id, rule_version)
);
