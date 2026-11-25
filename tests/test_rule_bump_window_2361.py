"""판 올림 창 (docs/paper.md 2.5.1, docs/infra.md 25.2361·2362).

판을 바꾸는 마이그레이션(판 번호·헌장 값)은 머리에 `-- 판 올림 창: YYYY-MM`(1월·7월, 2027-01 부터)·`-- 판 올림 예외: <까닭>`·
`-- 판 올림 사전 등록: <절>` 가운데 하나를 단다. 0097 까지(0077·0088·0095)는 이 규칙 전이라 표시가 없다 — 목록을 고정해 0098 부터 본다.
"""

from __future__ import annotations

import re
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent
# 문자열 리터럴·줄 주석을 걷어 낸 뒤 문장(;)마다 본다(25.2362 — 리터럴 안 `;`·따옴표 친 표 이름·upsert 를 놓쳤다)
_리터럴 = re.compile(r"'(?:[^']|'')*'")
_주석 = re.compile(r"--[^\n]*")
_표 = re.compile(r"""\bpaper_accounts\b|"paper_accounts"|`paper_accounts`|\[paper_accounts\]""", re.IGNORECASE)
_바꿈 = re.compile(r"\bUPDATE\b|\bON\s+CONFLICT\b", re.IGNORECASE)
_갈아끼움 = re.compile(r"\bREPLACE\s+INTO\b|\bINSERT\s+OR\s+REPLACE\b", re.IGNORECASE)  # 행을 통째로 다시 씀
# 판을 바꾸는 칸 — 판 번호, 그리고 판단을 만드는 헌장 값(헌장만 바꾸는 판 변경도 창을 따른다, docs/paper.md 2.5.1)
_판_칸 = re.compile(r"\b(rule_version|charter_json)\s*=", re.IGNORECASE)
_판_열 = re.compile(r"\b(rule_version|charter_json)\b", re.IGNORECASE)
창_줄 = re.compile(r"^--\s*판 올림 창:\s*(\d{4})-(\d{2})\s*$", re.MULTILINE)
예외_줄 = re.compile(r"^--\s*판 올림 (예외|사전 등록):(.*)$", re.MULTILINE)
창_달 = (1, 7)
첫_창 = (2027, 1)
규칙_전 = {"0077", "0088", "0095"}  # 0097 까지 판을 바꾼 마이그레이션 — 표시 없음
규칙_전_끝 = 97


def 판_변경(글: str) -> bool:
    본문 = _주석.sub("", _리터럴.sub("''", 글))
    return any(
        _표.search(문) and ((_바꿈.search(문) and _판_칸.search(문)) or (_갈아끼움.search(문) and _판_열.search(문)))
        for 문 in 본문.split(";")
    )


def 판정(글: str) -> str | None:
    """판을 바꾸지 않으면 None. 바꾸면 'ok' 또는 까닭"""
    if not 판_변경(글):
        return None
    머리 = "\n".join(글.splitlines()[:30])
    m = 예외_줄.search(머리)
    if m is not None:
        # 까닭(예외) 또는 사전 등록 절(그 글이 반영 시점을 정함)은 열 글자 넘게
        return "ok" if len(m.group(2).strip()) > 10 else f"`판 올림 {m.group(1)}:` 뒤 글이 열 글자를 넘지 않음"
    m = 창_줄.search(머리)
    if m is None:
        return "표시 없음 — `-- 판 올림 창: YYYY-MM`·`-- 판 올림 예외: <까닭>`·`-- 판 올림 사전 등록: <절>`"
    해, 달 = int(m.group(1)), int(m.group(2))
    if 달 not in 창_달:
        return f"창 달이 아님({해}-{달:02d} — 1월·7월만)"
    return "ok" if (해, 달) >= 첫_창 else f"첫 창 {첫_창[0]}-{첫_창[1]:02d} 앞({해}-{달:02d})"


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
    assert 판정("UPDATE paper_accounts SET name = '가상' WHERE id = 'A';") is None  # 이름(0081 꼴)은 판이 아니다
    assert 판정("UPDATE x SET rule_version = 1; -- paper_accounts") is None  # 다른 표
    assert 판정(올림) is not None and 판정(올림) != "ok"
    assert 판정("-- 판 올림 창: 2027-01\n" + 올림) == "ok"
    assert 판정("-- 판 올림 창: 2027-07\n" + 올림) == "ok"
    assert "1월·7월" in str(판정("-- 판 올림 창: 2027-03\n" + 올림))
    assert "첫 창" in str(판정("-- 판 올림 창: 2026-07\n" + 올림))  # 첫 창 앞의 해(25.2362)
    assert "첫 창" in str(판정("-- 판 올림 창: 2019-07\n" + 올림))
    assert 판정("-- 판 올림 예외: 체결 수량을 두 번 세던 장부 결함\n" + 올림) == "ok"
    assert 판정("-- 판 올림 예외: 0123456789\n" + 올림) != "ok"  # 열 글자는 넘지 않는다(25.2362)
    assert 판정("-- 판 올림 예외: 0123456789A\n" + 올림) == "ok"
    assert 판정("-- 판 올림 예외: 급함          \n" + 올림) != "ok"  # 끝 공백은 세지 않는다
    assert 판정("-- 판 올림 사전 등록: factors.md 12.53 반영 시점 조항\n" + 올림) == "ok"
    # 여러 줄에 걸친 UPDATE(0077 꼴)도 올림으로 본다
    assert 판정("UPDATE paper_accounts SET charter_json = '{}',\n  charter_since = 'x', rule_version = 2\nWHERE id = 'A';") is not None
    # 25.2362 — 리터럴 안 `;`, 따옴표 친 표 이름, upsert, 헌장만 바꾸기
    assert 판정("UPDATE paper_accounts SET charter_json = '{\"p\": \"넓게; 나눈다\"}', rule_version = 5 WHERE id='H';") not in (None, "ok")
    assert 판정('UPDATE "paper_accounts" SET rule_version = 5;') not in (None, "ok")
    assert 판정("INSERT INTO paper_accounts (id, rule_version) VALUES ('A', 5)"
              " ON CONFLICT(id) DO UPDATE SET rule_version = excluded.rule_version;") not in (None, "ok")
    assert 판정("UPDATE paper_accounts SET charter_json = json_set(charter_json, '$.top_n', 7) WHERE id='A';") not in (None, "ok")
    assert 판정("INSERT OR REPLACE INTO paper_accounts (id, rule_version) VALUES ('A', 5);") not in (None, "ok")
    assert 판정("INSERT OR IGNORE INTO paper_accounts (id, rule_version) VALUES ('Z9', 1);") is None  # 새 계좌(0096 꼴)
    # 머리(30줄) 밖의 표시는 세지 않는다
    assert 판정("\n" * 40 + "-- 판 올림 창: 2027-01\n" + 올림) != "ok"


def test_문서() -> None:
    글 = (뿌리 / "docs/paper.md").read_text()
    for 조각 in ("2.5.1 판 올림 창", "25.2361", "`-- 판 올림 창: YYYY-MM`", "`-- 판 올림 예외: <까닭>`", "test_rule_bump_window_2361"):
        assert 조각 in 글, 조각
    assert "판 올림 대기열" in (뿌리 / "docs/handoff.md").read_text()
