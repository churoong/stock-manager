"""리허설이 어떤 예외로 깨져도, 앞 단계에서 깨져도 실패를 적는다 (docs/infra.md 25.1306, 운영 감사 재현)."""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest

from scripts import restore_drill as rd


def test_잘린_백업도_실패로_적는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "stock-manager-essential-2026-10-12.sql.gz"
    body = "".join(f"INSERT INTO settings (key, value, updated_at) VALUES ('k{i}', 'v', 't');\n" for i in range(20000))
    blob = gzip.compress(("-- stock-manager 백업 (essential)\nBEGIN;\n" + body).encode())
    p.write_bytes(blob[: len(blob) // 2])  # 아티팩트가 잘렸다
    적힘: list = []
    monkeypatch.setattr(rd, "record", lambda result, path: 적힘.append(result))
    monkeypatch.setattr(sys, "argv", ["restore_drill.py", str(p), "--record-run"])
    assert rd.main() == 1
    assert len(적힘) == 1 and not 적힘[0].ok and 적힘[0].errors


def test_앞_단계_실패를_적는다(monkeypatch: pytest.MonkeyPatch) -> None:
    적힘: list = []
    monkeypatch.setattr(rd, "record", lambda result, path: 적힘.append(result))
    monkeypatch.setattr(sys, "argv", ["restore_drill.py", "--record-failure", "아티팩트 없음"])
    assert rd.main() == 1
    assert not 적힘[0].ok and 적힘[0].errors == ["아티팩트 없음"]


def test_워크플로가_앞_단계_실패를_적는다() -> None:
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/restore-drill.yml").read_text(encoding="utf-8")
    assert "steps.drill.outcome != 'failure'" in text and "--record-failure" in text and "id: drill" in text
