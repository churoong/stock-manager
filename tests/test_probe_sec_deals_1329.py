"""SEC 일별 양식 색인 탐침의 고정 폭 읽기 (docs/infra.md 25.1329, 20회차 P5)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("probe_sec_deals", ROOT / "scripts" / "probe_sec_deals.py")
probe = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(probe)  # type: ignore[union-attr]

색인 = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
SC 14D9     ACME CORP                                                     1234567     20250715    edgar/data/1234567/0001.txt
SC TO-T     ACME CORP                                                     1234567     20250715    edgar/data/1234567/0002.txt
SC TO-T     BIG BUYER HOLDINGS INC                                        7654321     20250715    edgar/data/1234567/0002.txt
10-K        OTHER CO                                                      1111        20250715    edgar/data/1111/0003.txt
"""


def test_양식_이름의_빈칸과_회사_이름의_빈칸을_가른다() -> None:
    rows = probe.parse_index(색인)
    assert rows[0] == ("SC 14D9", "ACME CORP", "1234567", "edgar/data/1234567/0001.txt")
    assert rows[2] == ("SC TO-T", "BIG BUYER HOLDINGS INC", "7654321", "edgar/data/1234567/0002.txt")
    assert [r[0] for r in rows if probe.relevant(r[0])] == ["SC 14D9", "SC TO-T", "SC TO-T"]


def test_머리가_없으면_빈_목록() -> None:
    assert probe.parse_index("그냥 글\n---\nSC 14D9 X 1 2 3") == []


def test_분기_경로() -> None:
    assert probe.index_url("20250715").endswith("/2025/QTR3/form.20250715.idx")
    assert "/QTR1/" in probe.index_url("20250331") and "/QTR4/" in probe.index_url("20251001")
