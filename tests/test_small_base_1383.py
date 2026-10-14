"""작은 기저 — 기록만 (docs/infra.md 25.1383, 25회차 P3)."""

from __future__ import annotations

from pathlib import Path

from batch.services import scoring as sc


def test_표식은_고정한_문턱으로_모르면_거짓() -> None:
    assert sc.small_base_flags(1e8, 1e11, None, None) == {"oi": True, "equity": False}  # 영업이익률 0.1%
    assert sc.small_base_flags(5e9, 1e11, 4.0, 100.0) == {"oi": False, "equity": True}  # 5%·자본 4%
    assert sc.small_base_flags(-1.0, 1e11, -4.0, 100.0) == {"oi": False, "equity": False}  # 음수는 다른 규칙
    assert sc.small_base_flags(None, None, None, None) == {"oi": False, "equity": False}


def test_시장별_상위와_전체의_몫() -> None:
    flags = {i: {"oi": i == 0, "equity": False} for i in range(20)}
    totals = {i: 100.0 - i for i in range(20)}  # 0 이 1등
    기록 = sc.small_base_report(flags, totals, dict.fromkeys(range(20), "KOSPI"))
    assert 기록["KOSPI"] == {"n": 20, "top_n": 2, "oi_top": 0.5, "oi_all": 0.05, "equity_top": 0.0, "equity_all": 0.0}


def test_점수_작업이_기록하고_점수는_그대로() -> None:
    원문 = (Path(__file__).resolve().parents[1] / "batch/jobs/scores.py").read_text()
    assert '"small_base": sc.small_base_report(' in 원문
    assert "small_base[stock_id] = sc.small_base_flags(" in 원문
