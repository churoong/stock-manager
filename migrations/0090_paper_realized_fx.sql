-- 가상모의투자 매도의 실현손익 가운데 **환차손익 몫** (docs/paper.md 6장, docs/infra.md 25.1693, 2026-10-22 사용자 지시
-- "모의투자에 실현손익이나 기간실현손익을 보는게 없네? 이것도 당연히 있어야지").
-- CLAUDE.md "해외 종목은 환차손익을 주가 손익과 분리해서 표시" — 실현손익(`realized_krw`) = 주가 몫 + 환차 몫.
-- 환차 몫 = 현지 통화 평균 원가 × (매도 환율 − 매수 평균 환율), 원화 종목은 0. 웹은 계산하지 않는다 — 배치가 체결 때 적고,
-- 이 열이 생기기 전 매도는 배치가 한 번 채운다(아래 부분 색인으로 남은 것만 찾는다 — 다 채우면 읽기 0)
ALTER TABLE paper_fills ADD COLUMN realized_fx_krw REAL;
CREATE INDEX IF NOT EXISTS idx_paper_fills_fx_pending ON paper_fills (account_id)
    WHERE side = 'sell' AND realized_fx_krw IS NULL;
