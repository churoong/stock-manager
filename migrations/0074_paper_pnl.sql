-- 모의 계좌 화면의 계좌별 손익 (docs/paper.md 6장, docs/infra.md 25.1455, 2026-10-15 사용자 지시 "각 모의A나 모의B의 보유내역, 거래내역, 평가손익").
-- 웹은 계산하지 않는다(CLAUDE.md) — 배치가 체결 때 실현손익을, 날마다 계좌의 실현·평가손익 합을 적는다.
-- 매도 체결의 실현손익(원) = 현금 영향 − 평균 원가(원) × 판 수량 / 보유 수량. 매수는 비어 있다
ALTER TABLE paper_fills ADD COLUMN realized_krw REAL;
-- 그날까지 실현손익 합 · 보유 평가손익(평가액 − 원가) 합
ALTER TABLE paper_daily ADD COLUMN realized_krw REAL;
ALTER TABLE paper_daily ADD COLUMN unrealized_krw REAL;
