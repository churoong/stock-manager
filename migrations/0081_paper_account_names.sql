-- 계좌 이름을 이름 규칙에 맞춘다 — "모의 A …" → "가상 A …" (2026-10-16 사용자 지시: 우리 계좌는 "가상모의투자",
-- 한국투자증권 모의계좌는 "KIS모의투자", CLAUDE.md 매매 규칙·docs/infra.md 25.1479). 이름 칸만 바꾼다 — id·규칙·장부는 그대로
UPDATE paper_accounts SET name = '가상' || substr(name, 3) WHERE name LIKE '모의 %';
