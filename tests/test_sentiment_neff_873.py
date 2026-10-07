"""감성 유효 기사 수(Kish n_eff) — 기록만 한다 (docs/infra.md 25.873, 8회차 4 1단계)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from batch.services import sentiment as st


def test_고르면_건수에_가깝고_쏠리면_작다() -> None:
    assert st.effective_n([1.0] * 10) == pytest.approx(10.0)
    assert st.effective_n([]) is None
    지금 = datetime(2026, 10, 1, tzinfo=UTC)
    # 오늘 1건 + 25일 전 4건 — 원래 건수 5건이라 값을 내지만 유효 기사 수는 2 아래
    기사 = [st.ScoredArticle(지금, 0.9)] + [st.ScoredArticle(지금 - timedelta(days=25), 0.1)] * 4
    agg = st.aggregate(기사, 지금)
    assert agg.sentiment is not None and agg.article_count == 5
    assert agg.effective_n is not None and agg.effective_n < 2
    assert agg.max_weight_share is not None and agg.max_weight_share > 0.7


def test_분포_요약만_남긴다() -> None:
    요약 = st.effective_n_summary([1.5, 3.0, 8.0, 12.0])
    assert 요약["n"] == 4 and 요약["below_min"] == 2 and 요약["below_min_share"] == 0.5
    assert st.effective_n_summary([]) == {"n": 0}
