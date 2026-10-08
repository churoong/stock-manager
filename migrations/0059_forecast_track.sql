-- 예측 성적표의 종목 누계 (docs/analysis.md 11.3, docs/infra.md 25.1042). 의견 행(`stock_verdicts.detail_json`)에 두었더니
-- 의견을 지우고 다시 쓰는 길(유니버스에서 빠짐, 뒤 묶음 실패)에서 그 종목의 누계가 통째로 사라졌다(교차검증 감사).
-- 지우지 않는다 — 그날 평가가 더해진 종목만 덮어쓴다.
--   track_json  {"capm": {"1": [n, n_range, in68, in90, dir_n, dir_hit, abs_err_sum], ...}, ...}
CREATE TABLE IF NOT EXISTS forecast_track (
    stock_id   INTEGER PRIMARY KEY REFERENCES stocks (id),
    country    TEXT NOT NULL,
    track_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
