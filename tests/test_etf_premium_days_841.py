"""위성 ETF 근거표의 괴리율 일수는 저장된 프로필의 `premium_days` 로 적는다 (docs/infra.md 25.841, 0042)."""

from __future__ import annotations

from batch.jobs import etf as job
from batch.jobs import etf_satellite as sat


def _행(premium_days: int | None) -> dict:
    return {"symbol": "069500", "etf_name": "KODEX 200", "category": "코스피 200", "total_assets": 1e12,
            "turnover_est": 1e10, "premium_abs_avg": 0.001, "days_observed": 20, "listed_3y_ago": 1,
            "premium_days": premium_days}  # fmt: skip


def test_저장된_일수를_읽는다_옛_행은_예전처럼() -> None:
    assert sat.kr_input_from_row(_행(10)).premium_days == 10
    assert sat.kr_input_from_row(_행(None)).premium_days is None


def test_프로필에_저장하는_열과_값이_맞다() -> None:
    assert job._PROFILE_COLS_KR.endswith("premium_days")
    assert "premium_days = excluded.premium_days" in job._PROFILE_CONFLICT_KR
