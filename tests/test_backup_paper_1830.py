"""필수 백업에 가상모의투자·KIS모의투자 표가 빠짐없이 든다 (docs/infra.md 25.1830).

KIS모의투자 장부(`paper_broker_orders`·`paper_broker_daily`)·판 이력(`paper_rule_versions`)·운용 요약(`paper_desk`)이 빠져
있었다 — 표를 새로 만들 때 백업 목록을 고치지 않아도 아무 검사가 깨지지 않았다(기존 대조는 웹이 쓰는 표만 본다).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import backup_db  # noqa: E402
import restore_backup  # noqa: E402

#: 마이그레이션 안에서 만들었다 이름을 바꾸는 임시 표 (0073: paper_decisions_new → paper_decisions)
임시표 = {"paper_decisions_new"}


def _모의표() -> set[str]:
    표 = set()
    for p in sorted((ROOT / "migrations").glob("*.sql")):
        표 |= set(re.findall(r"CREATE TABLE IF NOT EXISTS (paper_\w+)", p.read_text(encoding="utf-8")))
    return 표 - 임시표


def test_마이그레이션의_모의_표는_모두_필수_백업에() -> None:
    모의 = _모의표()
    assert len(모의) >= 12
    assert 모의 <= set(backup_db.ESSENTIAL_TABLES), sorted(모의 - set(backup_db.ESSENTIAL_TABLES))


def test_모의_입금_장부는_복구가_건너뛴_행을_크게_알린다() -> None:
    assert "paper_cash_flows" in restore_backup.사람이_넣은_표
    글 = restore_backup.건너뜀_알림({"paper_cash_flows": 3}, {"paper_cash_flows": 2})
    assert 글.startswith("⚠") and "paper_cash_flows" in 글
