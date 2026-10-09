-- 종목별 유니버스 이력 찾기에 색인 (docs/infra.md 25.1050). `universe_members` 에는 (snapshot_date, stock_id) 고유 색인뿐이라
-- "이 종목의 스냅샷" 을 찾는 질의가 종목마다 표 전체(스냅샷 수 × 종목 수, 십수만 행)를 훑었다.
--   daily._drop_never_priced     미국 일일 배치가 못 받은 심볼마다 COUNT(*) — 2026-10-08 미국 일일 배치 한 번 Turso 3,399만 행 읽기의 대부분
--   analyze_extra 의 유니버스 판정  종목마다 MAX(snapshot_date)
CREATE INDEX IF NOT EXISTS idx_universe_stock ON universe_members (stock_id, snapshot_date);
