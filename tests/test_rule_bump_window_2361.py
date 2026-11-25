"""판 올림 창 (docs/paper.md 2.5.1, docs/infra.md 25.2361).

판을 올리는 마이그레이션은 머리에 `-- 판 올림 창: YYYY-MM`(1월·7월) 또는 `-- 판 올림 예외: <까닭>` 을 단다. 0097 까지의 판 올림
(0077·0088·0095)은 이 규칙 전이라 표시가 없다 — 목록을 고정해 새 것만 본다.
"""

from __future__ import annotations

import re
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent
판_올림 = re.compile(r"UPDATE\s+paper_accounts\s+SET\b[^;]*\brule_version\s*=", re.IGNORECASE | re.DOTALL)
창_줄 = re.compile(r"^--\s*판 올림 창:\s*(\d{4})-(\d{2})\s*$", re.MULTILINE)
예외_줄 = re.compile(r"^--\s*판 올림 예외:\s*(\S.{9,})$", re.MULTILINE)
창_달 = (1, 7)
규칙_전 = {"0077", "0088", "0095"}  # 0097 까지 판을 올린 마이그레이션 — 표시 없음
규칙_전_끝 = 97


def 판정(글: str) -> str | None:
    """판을 올리지 않으면 None. 올리면 'ok' 또는 까닭"""
    if not 판_올림.search(글):
        return None
    머리 = "\n".join(글.splitlines()[:30])
    if 예외_줄.search(머리):
        return "ok"
    m = 창_줄.search(머리)
    if m is None:
        return "표시 없음 — `-- 판 올림 창: YYYY-MM` 또는 `-- 판 올림 예외: <까닭>`"
    return "ok" if int(m.group(2)) in 창_달 else f"창 달이 아님({m.group(1)}-{m.group(2)} — 1월·7월만)"


def _마이그레이션() -> list[Path]:
    return sorted((뿌리 / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))


def test_규칙_전_판_올림_목록이_그대로다() -> None:
    전 = {p.name[:4] for p in _마이그레이션() if int(p.name[:4]) <= 규칙_전_끝 and 판정(p.read_text()) is not None}
    assert 전 == 규칙_전


def test_새_판_올림은_창이나_예외를_적는다() -> None:
    for p in _마이그레이션():
        if int(p.name[:4]) <= 규칙_전_끝:
            continue
        결과 = 판정(p.read_text())
        assert 결과 in (None, "ok"), f"{p.name}: {결과} (docs/paper.md 2.5.1)"


def test_판정_자체() -> None:
    올림 = "UPDATE paper_accounts SET rule_version = 5 WHERE id = 'A';"
    assert 판정("CREATE TABLE x (a INT);") is None
    assert 판정("INSERT INTO paper_accounts (id, rule_version) VALUES ('Q', 1);") is None  # 새 계좌는 올림이 아니다
    assert 판정(올림) is not None and 판정(올림) != "ok"
    assert 판정("-- 판 올림 창: 2027-01\n" + 올림) == "ok"
    assert 판정("-- 판 올림 창: 2027-07\n" + 올림) == "ok"
    assert "1월·7월" in str(판정("-- 판 올림 창: 2027-03\n" + 올림))
    assert 판정("-- 판 올림 예외: 체결 수량을 두 번 세던 장부 결함\n" + 올림) == "ok"
    assert 판정("-- 판 올림 예외: 급함\n" + 올림) != "ok"  # 까닭은 열 글자 넘게
    # 여러 줄에 걸친 UPDATE(0077 꼴)도 올림으로 본다
    assert 판정("UPDATE paper_accounts SET charter_json = '{}',\n  charter_since = 'x', rule_version = 2\nWHERE id = 'A';") is not None
    # 머리(30줄) 밖의 표시는 세지 않는다
    assert 판정("\n" * 40 + "-- 판 올림 창: 2027-01\n" + 올림) != "ok"


def test_문서() -> None:
    글 = (뿌리 / "docs/paper.md").read_text()
    for 조각 in ("2.5.1 판 올림 창", "25.2361", "`-- 판 올림 창: YYYY-MM`", "`-- 판 올림 예외: <까닭>`", "test_rule_bump_window_2361"):
        assert 조각 in 글, 조각
    assert "판 올림 대기열" in (뿌리 / "docs/handoff.md").read_text()
