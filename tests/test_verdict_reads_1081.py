"""종목 분석 의견의 날짜 고르기 질의가 표를 통째로 훑지 않는다 (docs/infra.md 25.1081, 마이그레이션 0061)."""

from __future__ import annotations

import json

from batch.jobs import verdicts
from tests import test_sql_schema as t


def _계획(sql: str, args: list) -> list[str]:
    db = t.스키마올리기()
    return [r[-1] for r in db.execute("EXPLAIN QUERY PLAN " + sql, args)]


def test_세_질의가_색인을_탄다() -> None:
    감성 = _계획(verdicts.SENTIMENT_GAP_SQL, ["2026-10-02", "KR"])
    assert 감성[0].startswith("SEARCH ss USING INDEX idx_sentiment_scores_date"), 감성
    밴드 = _계획(verdicts.BAND_SQL, ["KR", "KR"])
    assert not any(x.startswith("SCAN v") for x in 밴드) and "idx_valuation_bands_date" in 밴드[0], 밴드
    종가 = _계획(verdicts.PRICE_BACK_SQL, ["[1, 2]", "2026-09-01", "2026-10-09"])
    assert 종가[0].startswith("SCAN j") and "(stock_id=? AND date>? AND date<?)" in 종가[1], 종가


def test_엇갈림_종가는_준_종목만_감성은_날짜_내림차순() -> None:
    db = t.스키마올리기()
    for i, 나라 in ((1, "KR"), (2, "KR"), (3, "US")):
        db.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at)"
                   " VALUES (?, ?, 'X', ?, 'KRW', 't', 't')", [i, f"T{i}", 나라])  # fmt: skip
        for d in ("2026-09-01", "2026-10-01"):
            db.execute("INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
                       " VALUES (?, ?, 10, 'KRW', 't', 't')",
                       [i, d])  # fmt: skip
    rows = db.execute(verdicts.PRICE_BACK_SQL, [json.dumps([1]), "2026-08-01", "2026-10-09"]).fetchall()
    assert {r[0] for r in rows} == {1} and len(rows) == 2
    assert "ORDER BY ss.as_of_date DESC" in verdicts.SENTIMENT_GAP_SQL  # 종목마다 첫 행 = 최신 (setdefault)
