"""판 올림 창 (docs/paper.md 2.5.1, docs/infra.md 25.2361~2364).

판을 바꾸는 마이그레이션(판 번호·헌장 값·규칙 열·행값 대입, 계좌 행을 통째로 다시 쓰는 REPLACE·DELETE·표 다시 짓기)은 머리에 `-- 판 올림 창: YYYY-MM`(1월·7월, 2027-01 부터)·`-- 판 올림 예외: <까닭>`·
`-- 판 올림 사전 등록: <절>` 가운데 하나를 단다. 0097 까지(0077·0088·0095)는 이 규칙 전이라 표시가 없다 — 목록을 고정해 0098 부터 본다.
"""

from __future__ import annotations

import re
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent
# 문자열 리터럴·줄 주석·블록 주석을 **왼쪽부터 한 번에** 걷은 뒤 문장(;)마다 본다(25.2362·2363 — 따로 걷으면 주석 속
# 홑따옴표가 뒤 SQL 을 리터럴로 삼켰다)
_걷을것 = re.compile(r"'(?:[^']|'')*'|--[^\n]*|/\*.*?\*/", re.DOTALL)
_표 = re.compile(r"""\bpaper_accounts\b|"paper_accounts"|`paper_accounts`|\[paper_accounts\]""", re.IGNORECASE)
_바꿈 = re.compile(r"\bUPDATE\b", re.IGNORECASE)  # upsert 의 `DO UPDATE SET` 도 여기서 잡힌다
# 행을 통째로 다시 씀 — 열 목록에 없는 판·헌장도 기본값으로 돌아가므로 열과 상관없이 판 변경(25.2363).
# 지우고 다시 넣기(DELETE 뒤 INSERT)도 같은 효과라 계좌 행을 지우는 문장도 판 변경으로 본다(25.2364)
# 표 다시 짓기(DROP 뒤 새 표를 RENAME — 0073 이 쓴 꼴)도 같다(25.2365). DELETE·DROP·RENAME 은 **대상 표가 paper_accounts** 일 때만
# (다른 표를 지우며 하위 질의로 읽기만 하는 문장은 아님, 25.2365)
_표이름 = r"""["`\[]?(?:main\.)?paper_accounts\b["`\]]?"""
_갈아끼움 = re.compile(
    r"\bREPLACE\s+INTO\b|\bINSERT\s+OR\s+REPLACE\b|\bDELETE\s+FROM\s+" + _표이름 + r"|\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?"
    + _표이름 + r"|\bALTER\s+TABLE\s+\S+\s+RENAME\s+TO\s+" + _표이름,
    re.IGNORECASE,
)
# 판을 바꾸는 칸 — 판 번호, 그리고 판단을 만드는 헌장 값(헌장만 바꾸는 판 변경도 창을 따른다, docs/paper.md 2.5.1)
# 판단을 정하는 열 — 판 번호·헌장, 그리고 규칙 열(매도 규칙·진입·상위 N·선정, 0070·0075 — 판을 올리지 않고 이것만 바꾸면
# 더 나쁜 위반이라 같이 본다, 25.2364). 따옴표 친 열(25.2363)과 행값 대입 `SET (a, b) = (…)`(25.2364)도
_열 = r"(rule_version|charter_json|sell_rule|entry_rule|top_n|selection)"
_판_칸 = re.compile(
    r"""["`\[]?\b""" + _열 + r"""\b["`\]]?\s*=|\(\s*[^()]*\b""" + _열 + r"""\b[^()]*\)\s*=""", re.IGNORECASE
)
창_줄 = re.compile(r"^--\s*판 올림 창:\s*(\d{4})-(\d{2})\s*$", re.MULTILINE)
예외_줄 = re.compile(r"^--\s*판 올림 (예외|사전 등록):(.*)$", re.MULTILINE)
창_달 = (1, 7)
첫_창 = (2027, 1)
규칙_전 = {"0077", "0088", "0095"}  # 0097 까지 판을 바꾼 마이그레이션 — 표시 없음
규칙_전_끝 = 97


def 판_변경(글: str) -> bool:
    본문 = _걷을것.sub(lambda m: "''" if m.group(0).startswith("'") else " ", 글)
    return any(
        _표.search(문) and ((_바꿈.search(문) and _판_칸.search(문)) or _갈아끼움.search(문)) for 문 in 본문.split(";")
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
    # 25.2363 — 걷기 하나하나를 잡는 꼴(각 걷기를 빼면 깨진다)
    assert 판정("UPDATE paper_accounts SET name = 'a;b', rule_version = 5 WHERE id='A';") not in (None, "ok")  # 리터럴 걷기
    assert 판정("UPDATE x -- paper_accounts\nSET rule_version = 1;") is None  # 줄 주석 걷기
    assert 판정("UPDATE x /* paper_accounts */ SET rule_version = 1;") is None  # 블록 주석 걷기
    assert 판정("-- H's charter bump\nUPDATE paper_accounts SET rule_version = 5 WHERE id = 'H';") not in (None, "ok")
    assert 판정("/* don't */ UPDATE paper_accounts SET rule_version = 5 WHERE id = 'H';") not in (None, "ok")
    assert 판정('UPDATE paper_accounts SET "rule_version" = 5;') not in (None, "ok")  # 따옴표 친 열
    assert 판정("INSERT OR REPLACE INTO paper_accounts (id, name) VALUES ('A', 'x');") not in (None, "ok")
    # 25.2364 — 리터럴 안의 `--`·`/*`(주석 먼저 걷기로 되돌리면 뒤 SQL 이 지워진다), 행값 대입, 지우고 다시 넣기, 규칙 열
    # (판 칸이 `--`·`/*` **뒤에만** 있어야 잡힌다 — 앞에 charter_json 이 있으면 걷기 순서와 상관없이 잡혀 시험이 되지 않는다)
    assert 판정("UPDATE paper_accounts SET name = 'a--b', rule_version = 5 WHERE id='H';") not in (None, "ok")
    assert 판정("UPDATE paper_accounts SET name = 'a/*b', rule_version = 5 WHERE id='H'; -- */") not in (None, "ok")
    assert 판정("UPDATE paper_accounts SET (rule_version, charter_since) = (5, '2027-01-04') WHERE id='H';") not in (None, "ok")
    assert 판정("INSERT INTO paper_accounts (id, rule_version) VALUES ('H', 5)"
              " ON CONFLICT(id) DO UPDATE SET (rule_version) = (5);") not in (None, "ok")
    assert 판정("DELETE FROM paper_accounts WHERE id='H'; INSERT INTO paper_accounts (id, rule_version) VALUES ('H', 5);") not in (None, "ok")
    for 열 in ("sell_rule = 'trend'", "entry_rule = 'x'", "top_n = 7", "selection = 'y'"):
        assert 판정(f"UPDATE paper_accounts SET {열} WHERE id='A';") not in (None, "ok"), 열
    # 25.2365 — REPLACE INTO, 표 다시 짓기, 대상 표로 좁힌 DELETE
    assert 판정("REPLACE INTO paper_accounts (id, name) VALUES ('A', 'x');") not in (None, "ok")
    assert 판정("DROP TABLE paper_accounts;") not in (None, "ok")
    assert 판정("CREATE TABLE paper_accounts_new (id TEXT);"
              " INSERT INTO paper_accounts_new SELECT id FROM paper_accounts;"
              " DROP TABLE IF EXISTS paper_accounts; ALTER TABLE paper_accounts_new RENAME TO paper_accounts;") not in (None, "ok")
    assert 판정("DELETE FROM paper_orders WHERE account_id NOT IN (SELECT id FROM paper_accounts);") is None
    assert 판정("ALTER TABLE paper_accounts RENAME TO paper_accounts_old;") is None  # 옮기기만(다시 짓기 앞 단계)은 아님
    # 머리 30줄 경계 — 30번째 줄의 표시는 세고 31번째는 세지 않는다
    assert 판정("\n" * 29 + "-- 판 올림 창: 2027-01\n" + 올림) == "ok"
    assert 판정("\n" * 30 + "-- 판 올림 창: 2027-01\n" + 올림) != "ok"
    # 머리(30줄) 밖의 표시는 세지 않는다
    assert 판정("\n" * 40 + "-- 판 올림 창: 2027-01\n" + 올림) != "ok"


def test_문서() -> None:
    글 = (뿌리 / "docs/paper.md").read_text()
    for 조각 in ("2.5.1 판 올림 창", "25.2361", "`-- 판 올림 창: YYYY-MM`", "`-- 판 올림 예외: <까닭>`", "test_rule_bump_window_2361",
                 "`-- 판 올림 사전 등록: <절>`", "2027-01 부터", "크기와 상관없이 바로", "`DELETE FROM` 은 열과 상관없이",
                 "25.1600 [권고] 1", "**켜는 문턱**은 원가 위"):
        assert 조각 in 글, 조각
    assert "판 올림 대기열" in (뿌리 / "docs/handoff.md").read_text()
