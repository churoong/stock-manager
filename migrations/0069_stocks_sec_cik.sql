-- 미국 종목의 SEC 고유번호(CIK) — 심볼이 바뀐 종목(SQ→XYZ)을 옛 행에 잇는 데 쓴다 (docs/infra.md 25.1411, 주간 작업 감사).
-- SEC `company_tickers.json` 은 지금 티커만 주므로 사라진 옛 티커의 CIK 를 나중에 알 길이 없다 — 미리 적어 둔다.
-- `us_shares` 가 심볼→CIK 를 이을 때마다 채운다. 국내 종목은 비어 있다
ALTER TABLE stocks ADD COLUMN sec_cik TEXT;
CREATE INDEX IF NOT EXISTS idx_stocks_sec_cik ON stocks (sec_cik);
