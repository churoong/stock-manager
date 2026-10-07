"""웹이 쓰는 표는 **백업에 담기거나, 안 담는 사유가 있어야 한다** (docs/infra.md 25.130).

**왜 웹인가.** 사람 손이 닿는 것은 거의 다 웹으로 들어온다 — 매매·배당·관심종목·설정·
스크리너 조건. 배치가 쓰는 표는 대부분 다시 받거나 다시 계산할 수 있지만(시세·점수·신호),
사용자가 손으로 넣은 것은 **어디서도 다시 만들 수 없다**(docs/backup.md).

**이미 한 번 빠뜨렸다.** `screener_presets` 가 `ESSENTIAL_TABLES` 에 없었다(25.118).
그때 고친 방식은 `scripts/move_user_data.py` 의 목록과 대 보는 것이었는데, **두 목록에
모두 빠진 새 표**는 여전히 아무도 못 잡는다. 웹 소스를 훑으면 그 구멍이 막힌다 —
목록이 아니라 **실제로 쓰는 곳**을 보기 때문이다.

**주석을 지우고 본다.** 주석에 적어 둔 SQL 을 세면 거짓 양성이 나고, 그러면 사유 표에
있지도 않은 표를 적게 된다. 이 저장소에서 네 번 겪은 실수다(25.105·25.109·25.112·25.116).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent


def _백업모듈():
    spec = importlib.util.spec_from_file_location("backup_db", 뿌리 / "scripts" / "backup_db.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


백업 = _백업모듈()

#: 훑는 곳. 웹이 DB 에 쓰는 문장은 경로(`app/api`)와 질의 모음(`lib`)에 나뉘어 있다.
훑을곳 = ("web/app/api", "web/lib")

_주석 = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
#: `ON CONFLICT (...) DO UPDATE SET` 의 `UPDATE` 는 표 이름을 데리고 오지 않는다.
#: 앞의 `DO` 를 보고 걸러 낸다 — 이것을 안 걸면 `set` 이 표 이름으로 잡힌다.
#: `for (const update of ...)` 같은 평범한 TypeScript 도 같은 이유로 걸린다(`of`).
_쓰기 = re.compile(
    r"(?<!\bDO\s)\b(?:INSERT\s+INTO|INSERT\s+OR\s+\w+\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_][a-z0-9_]*)",
    re.I,
)
#: SQL·JS 의 예약어가 표 이름 자리에 잡힌 것. 표가 아니다
_표가_아님 = {"set", "of", "from", "where", "select", "values", "join", "on"}


def 웹이_쓰는_표() -> dict[str, set[str]]:
    """표 이름 → 그것을 쓰는 파일들. **주석은 지우고 센다.**"""
    나온것: dict[str, set[str]] = {}
    for 자리 in 훑을곳:
        for 길 in sorted((뿌리 / 자리).rglob("*.ts")):
            본문 = _주석.sub(" ", 길.read_text(encoding="utf-8"))
            for m in _쓰기.finditer(본문):
                이름 = m.group(1).lower()
                if 이름 in _표가_아님:
                    continue
                나온것.setdefault(이름, set()).add(str(길.relative_to(뿌리)))
    return 나온것


쓰는표 = 웹이_쓰는_표()


def test_읽어_냈다() -> None:
    """**0개면 아래가 공짜로 통과한다.** 훑기가 조용히 비면 그물이 아니라 장식이다."""
    assert len(쓰는표) >= 10, f"웹이 쓰는 표를 {len(쓰는표)}개밖에 못 찾았다 — 표기가 바뀌었나"
    # 사람이 손으로 넣는 대표적인 둘이 반드시 보여야 한다
    assert "trades" in 쓰는표 and "settings" in 쓰는표


def test_주석_속_SQL_은_세지_않는다() -> None:
    """거짓 양성이 나면 사유 표에 없는 표 이름을 적게 된다 (25.112 와 같은 실수)."""
    본문 = _주석.sub(" ", "// INSERT INTO ghost (a) VALUES (1)\n/* UPDATE ghost2 SET a=1 */\nSELECT 1")
    assert not _쓰기.findall(본문)


def test_DO_UPDATE_SET_을_표로_읽지_않는다() -> None:
    """`ON CONFLICT (k) DO UPDATE SET v = …` 의 `SET` 이 표 이름으로 잡히던 자리다."""
    본문 = "INSERT INTO settings (k) VALUES (?) ON CONFLICT (k) DO UPDATE SET v = excluded.v"
    assert [m.group(1) for m in _쓰기.finditer(본문)] == ["settings"]


@pytest.mark.parametrize("표", sorted(쓰는표), ids=sorted(쓰는표))
def test_담기거나_사유가_있다(표: str) -> None:
    담김 = 표 in 백업.ESSENTIAL_TABLES
    사유 = 백업.백업_제외_사유.get(표)
    assert 담김 or 사유, (
        f"웹이 `{표}` 에 쓰는데 백업(`ESSENTIAL_TABLES`)에 없고 사유도 없다.\n"
        f"  쓰는 곳: {', '.join(sorted(쓰는표[표]))}\n"
        "  사람이 넣은 값이면 ESSENTIAL_TABLES 에 넣고, 아니면 백업_제외_사유 에 **왜 잃어도 되는지**를 적어라.\n"
        "  (docs/infra.md 25.118 — screener_presets 가 이렇게 빠져 있었다)"
    )


def test_사유가_비어_있지_않다() -> None:
    """빈 문자열로 검사를 통과시키는 것을 막는다. 사유는 사람이 읽을 문장이어야 한다."""
    for 표, 사유 in 백업.백업_제외_사유.items():
        assert len(사유) > 20, f"{표} 의 사유가 너무 짧다"


def test_담긴_표를_사유_표에_또_넣지_않는다() -> None:
    """둘 다에 있으면 어느 쪽이 사실인지 알 수 없다."""
    겹침 = sorted(set(백업.백업_제외_사유) & set(백업.ESSENTIAL_TABLES))
    assert not 겹침, f"백업에 담으면서 '안 담는 사유' 도 적어 두었다: {겹침}"


def test_사유_표에_쓰지도_않는_표를_두지_않는다() -> None:
    """웹이 더 이상 안 쓰는 표의 사유가 남아 있으면, 다음 사람이 그것을 사실로 읽는다."""
    유령 = sorted(set(백업.백업_제외_사유) - set(쓰는표))
    assert not 유령, f"웹이 쓰지 않는 표의 사유가 남아 있다: {유령}"
