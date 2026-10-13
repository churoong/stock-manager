-- 국내 사업보고서의 **보고 기간 일수** (docs/infra.md 25.1286, 재무 감사 재현).
-- DART 다중회사 응답의 손익 행 `thstrm_dt`("2021.11.02 ~ 2021.12.31") 를 버려, 분할 신설 첫해(2개월)·결산월 변경(9개월)이
-- 12개월 해처럼 비교됐다 — E/P·S/P·ROE 가 6분의 1, 다음 해 매출·영업이익 성장이 +500%. 모르면 NULL(옛 행) — 예전처럼 본다
ALTER TABLE financials ADD COLUMN period_days INTEGER;
