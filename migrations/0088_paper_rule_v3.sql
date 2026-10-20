-- 판 올림 (docs/company.md 11장 절차 2, docs/infra.md 25.1576) — 판단을 바꾸는 결함 수정 두 가지를 한 판에 담는다.
-- ① 공시·지배구조부: 주간 사건 표(금요일에만 다시 씀)의 덮개만 보던 탓에 화~금 배치에서 "자료 없음" 이 되어 전환사채 반대가 꺼지고
--    같은 종목의 가결·부결이 요일마다 갈렸다. 덮개 끝 뒤를 날마다 공시 덮개로 잇고 사건을 보충한다(`jobs/company.py`).
--    → 공시부 가중이 있는 A·C·D·E 판 2 → 3 (헌장 값은 그대로 — 판단을 만드는 자료 쪽이 바뀌었다)
-- ② H 회사 가중치의 기준 평균을 표본 가중으로(`services/holding.py`) → H 판 1 → 2. 투표 회사 판이 올랐으므로 H 도 올린다
--    (docs/company.md 10장 — 투표 회사가 판을 올리면 H 도 판을 올린다). 헌장 지문이 그대로라 `charter_json.voters` 는 그대로
-- 판 이력(`paper_rule_versions`)은 다음 운용 요약 단계가 새 판의 시작 결정일을 적는다. 성과 분해·부서 성적표·관문은 판으로 거른다
UPDATE paper_accounts SET rule_version = 3 WHERE id IN ('A', 'C', 'D', 'E') AND rule_version = 2;
UPDATE paper_accounts SET rule_version = 2 WHERE id = 'H' AND rule_version = 1;
