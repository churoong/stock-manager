-- 업종 출처 (docs/data-sources.md 15절, batch/services/sectors.py).
--
-- stocks.sector 에는 중분류 이름(예: 전자부품·컴퓨터·통신장비)을 넣는다. 그 이름이 어디서 왔는지
-- 확인할 수 있게 원래 코드와 출처를 함께 남긴다. 근거표 원칙(CLAUDE.md 절대 규칙).

ALTER TABLE stocks ADD COLUMN sector_code TEXT;        -- 원래 코드. 예: KSIC 264, SIC 3571
ALTER TABLE stocks ADD COLUMN sector_source TEXT;      -- dart_company / sec_submissions
ALTER TABLE stocks ADD COLUMN sector_updated_at TEXT;
