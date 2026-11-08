-- 판 올림 (docs/company.md 11장 절차 2, docs/infra.md 25.2187) — 점수 판 12: 모멘텀 여섯째 지표를 변동성 조정 모멘텀에서
-- 에코 모멘텀으로 바꿨다(docs/factors.md 3.4·12.12 — 사전 등록 A/B 를 11-03 판정 실행에서 두 나라 모두 넘음).
-- 판단식은 그대로지만 판단을 만드는 점수(모멘텀 팩터·종합 점수·단기 신호의 모멘텀 ≥ 60)가 바뀐다 — 앞 판과 섞지 않는다.
--   A·C·D·E 판 3 → 4 (퀀트부 모멘텀 백분위·신호 후보·재무악화 매도), B·G 판 1 → 2 (점수 상위 배분),
--   H 판 2 → 3 (투표 회사가 판을 올리면 H 도 올린다 — 실전 관문 126평가가 새 판부터 다시 센다),
--   S·T 판 1 → 2 (단기 신호 모멘텀 조건). R1·R2(무작위 대조 — 규칙 그대로, 0088 선례)·X·Y(지수 국면만)는 그대로.
-- 헌장 값이 그대로라 `charter_json.voters` 지문은 그대로다(0088 과 같다). 판 이력(`paper_rule_versions`)은 다음 운용
-- 요약 단계가 새 판의 시작 결정일을 적는다. 성과 분해·부서 성적표·관문은 (계좌, 판)으로 거른다
UPDATE paper_accounts SET rule_version = 4 WHERE id IN ('A', 'C', 'D', 'E') AND rule_version = 3;
UPDATE paper_accounts SET rule_version = 2 WHERE id IN ('B', 'G', 'S', 'T') AND rule_version = 1;
UPDATE paper_accounts SET rule_version = 3 WHERE id = 'H' AND rule_version = 2;
