"""SEC 탐침이 8-K 접수 시각·항목을 찍는다 (docs/infra.md 25.1426, docs/factors.md 12.30 P2·P3)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

길 = Path(__file__).resolve().parent.parent / "scripts" / "probe_sec_filings.py"


def _모듈():
    spec = importlib.util.spec_from_file_location("probe_sec_filings", 길)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_8K_만_시각과_항목을_찍는다(capsys: pytest.CaptureFixture[str]) -> None:
    _모듈().show_8k({"form": ["10-Q", "8-K"], "acceptanceDateTime": ["a", "2026-07-30T16:31:02.000Z"],
                     "items": ["", "2.02,9.01"], "filingDate": ["2026-08-01", "2026-07-30"]})  # fmt: skip
    글 = capsys.readouterr().out
    assert "acceptanceDateTime=2026-07-30T16:31:02.000Z items='2.02,9.01'" in 글
    assert "10-Q" not in 글 and "가장 오래된 filingDate 2026-07-30" in 글
