"""아무도 부르지 않는 함수가 늘어나지 않는가 (`batch/`·`scripts/`).

**왜 있나.** 2026-09-21 에 훑어 보니 부르는 곳이 한 군데도 없는 함수가 **열 개** 있었다.
그냥 무게만 되는 것도 있었지만, 둘은 달랐다.

1. **덫**: `batch/core/db.py` 에 `upsert_price()`·`upsert_stock()` 이 있었다. 한 행에
   왕복 한 번(종목은 두 번)이다. 이 모듈은 배치가 전부 import 하는 곳이라 **이름만 보고
   반복문 안에서 부르기 딱 좋다.** D1 하루 쓰기 한도를 두 배 넘긴 일(25.20)이 바로 쓰기를
   헤프게 쓴 결과였다. 실제로 쓰는 길은 `bulk_upsert_prices` 와 `jobs/universe.py` 다
2. **안 쓰이는 방어선**: `services/pit.py` 의 `coverage()`·`latest_known_date()` 는
   `scripts/check_pit.py` 에 쓰라고 만들어 두고 정작 아무도 부르지 않았다.
   같은 일을 `assert_no_lookahead` 에서도 겪었다(25.35)

**죽은 코드가 위험한 이유는 자리를 차지해서가 아니라 읽는 사람을 속여서다.**
있으니까 쓰는 길이라고 읽고, 검증된 적 없는 길로 들어간다.

이 테스트는 **지금 수를 못 박는다.** 늘리려면 이 목록에 이유를 적어야 한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent

#: 부르는 곳이 없어도 괜찮은 것. **이유를 적어야 들어올 수 있다**
두기로_한_것 = {
    "mask": "설정값을 로그에 찍을 때 길이만 드러내는 네 줄짜리다."
    " 지금 비밀값을 로그에 찍는 곳이 없어 안 쓰이지만, 필요해지는 날 없으면 날것으로 찍게 된다",
    "skip_notice": "휴장·건너뜀을 사람에게 알리는 문장. 수동 실행 때 쓰라고 만들었다"
    " (formatter.py 주석). 문장을 만들 뿐 아무것도 부르지 않아 덫이 되지 않는다",
}

#: 진입점·규약상 이름. 부르는 곳을 글자로 찾을 수 없다
규약 = {"main", "run"}


def 파이썬_파일들() -> list[Path]:
    return sorted(
        p
        for d in ("batch", "scripts")
        for p in (뿌리 / d).rglob("*.py")
        if "__pycache__" not in p.parts
    )


def 쓰이는가(이름: str, 전체: str, 정의모음: str) -> bool:
    """이름이 **정의 말고 다른 데서** 나타나는가.

    정의는 파일마다 하나씩 셀 수 있으므로(같은 이름이 여러 모듈에 있을 수 있다)
    그 수를 빼고 남는 등장이 있으면 누군가 부르는 것으로 본다.
    """
    정의수 = len(re.findall(rf"^(?:async )?def {re.escape(이름)}\(", 정의모음, re.M))
    return len(re.findall(rf"\b{re.escape(이름)}\b", 전체)) > 정의수


def 아무도_안_부르는_함수() -> list[str]:
    파일 = 파이썬_파일들()
    본문 = {p: p.read_text(encoding="utf-8") for p in 파일}
    바깥 = " ".join(
        p.read_text(encoding="utf-8")
        for d in ("tests", "web", ".github")
        for p in (뿌리 / d).rglob("*")
        if p.is_file()
        and p.suffix in (".py", ".ts", ".tsx", ".yml")
        and not {"__pycache__", "node_modules"} & set(p.parts)
    )
    전체 = " ".join(본문.values()) + bytes(0).decode() + 바깥

    안쓰임 = []
    모든소스 = " ".join(본문.values())
    for 경로, 소스 in 본문.items():
        for m in re.finditer(r"^(?:async )?def ([a-zA-Z가-힣][\w가-힣]*)\(", 소스, re.M):
            이름 = m.group(1)
            if 이름 in 규약 or 이름.startswith("_") or 이름 in 두기로_한_것:
                continue
            if not 쓰이는가(이름, 전체, 모든소스):
                줄 = 소스[: m.start()].count("\n") + 1
                안쓰임.append(f"{경로.relative_to(뿌리)}:{줄}  {이름}()")
    return sorted(안쓰임)


def test_훑을_것이_있다() -> None:
    """훑기가 조용히 0개를 내면 아래가 무조건 통과한다."""
    파일 = 파이썬_파일들()

    assert len(파일) > 50, f"{len(파일)}개만 훑었다"
    assert any(p.name == "db.py" for p in 파일)


class Test거르개가_실제로_잡는가:
    """이 검사가 없으면 거르개가 망가져 **아무것도 못 찾으면서** 통과한다."""

    def test_정의만_있으면_안_쓰인다(self) -> None:
        정의 = "def 외톨이(x):\n    return x\n"

        assert 쓰이는가("외톨이", 정의, 정의) is False

    def test_한_번이라도_부르면_쓰인다(self) -> None:
        정의 = "def 외톨이(x):\n    return x\n"

        assert 쓰이는가("외톨이", 정의 + "\n외톨이(1)", 정의) is True

    def test_같은_이름이_두_모듈에_있어도_센다(self) -> None:
        # 정의 수를 빼지 않으면 두 번 정의된 것만으로 "쓰인다" 가 된다
        정의 = "def 겹친이름(x):\n    pass\ndef 겹친이름(y):\n    pass\n"

        assert 쓰이는가("겹친이름", 정의, 정의) is False
        assert 쓰이는가("겹친이름", 정의 + " 겹친이름()", 정의) is True

    def test_들여쓴_def_는_정의로_세지_않는다__알려진_한계(self) -> None:
        """**이 거르개의 한계를 못 박아 둔다.**

        정의를 `^def` 로 세므로 클래스 메서드처럼 들여쓴 `def` 는 정의로 안 잡힌다.
        그래서 같은 이름의 메서드가 어딘가 있으면 **죽은 모듈 함수가 산 것처럼 보인다.**
        놓치는 쪽이지 없는 것을 있다고 하는 쪽은 아니라서 그대로 둔다.
        모르고 있다가 "왜 이건 안 잡혔지" 하는 것보다 적어 두는 편이 낫다.
        """
        정의 = "def 이름(x):\n    pass\n"
        메서드도있음 = 정의 + "class A:\n    def 이름(self):\n        pass\n"

        assert 쓰이는가("이름", 메서드도있음, 메서드도있음) is True  # 사실은 아무도 안 부른다

    def test_부분_문자열에_속지_않는다(self) -> None:
        # `run` 이 `runner` 에 걸리면 죽은 함수를 산 것으로 읽는다
        정의 = "def 살핌(x):\n    pass\n"

        assert 쓰이는가("살핌", 정의 + " 살핌이(1)", 정의) is False


def test_죽은_함수가_늘지_않았다() -> None:
    죽은것 = 아무도_안_부르는_함수()

    assert not 죽은것, (
        "부르는 곳이 없는 함수가 생겼다. 지우거나, 쓰는 곳에 연결하거나,"
        " 남길 이유를 tests/test_dead_code.py 의 `두기로_한_것` 에 적는다"
        " (docs/infra.md 25.48):\n  " + "\n  ".join(죽은것)
    )


@pytest.mark.parametrize("이름", sorted(두기로_한_것))
def test_두기로_한_것은_이유가_적혀_있다(이름: str) -> None:
    assert 두기로_한_것[이름].strip(), f"{이름} 의 이유가 비어 있다"
    # 목록이 낡지 않게: 정말 그 이름이 아직 있는가
    있다 = any(
        re.search(rf"^def {re.escape(이름)}\(", p.read_text(encoding="utf-8"), re.M)
        for p in 파이썬_파일들()
    )
    assert 있다, f"{이름} 은 이미 사라졌다 — 목록에서 지운다"


class Test한_행씩_쓰는_지름길을_다시_두지_않는다:
    """25.20 에서 배운 것. 쓰기는 헤프게 쓰면 하루 한도가 금방 간다."""

    def test_core_db_에_한_행짜리_시세_쓰기가_없다(self) -> None:
        소스 = (뿌리 / "batch" / "core" / "db.py").read_text(encoding="utf-8")

        assert "def upsert_price(" not in 소스
        assert "def upsert_stock(" not in 소스

    def test_묶어_넣는_길은_그대로_있다(self) -> None:
        # 지운 것이 대체재를 함께 지우지 않았는지 본다
        소스 = (뿌리 / "batch" / "core" / "db.py").read_text(encoding="utf-8")

        assert "def bulk_upsert_prices(" in 소스


# ----------------------------------------------------------------------
# 웹 (2026-09-23, docs/infra.md 25.153)
#
# 위 훑기는 `batch/`·`scripts/` 만 본다. **`web/lib` 는 한 번도 안 봤다.**
# 25.123 에서 `FREE_DB_BYTES` 가 "정의만 있고 부르는 곳이 없었다" 로 걸렸고,
# 25.152 를 쓰다가 `web/lib/db.ts` 의 `export const __internal` 이 같은 상태인 것을 봤다.
# 화면 쪽이라고 덜 위험하지 않다 — 오히려 **있으니까 쓰는 길이라고 읽기** 쉽다.
# ----------------------------------------------------------------------

#: 웹에서 부르는 곳이 없어도 괜찮은 것. **이유를 적어야 들어올 수 있다**
웹_두기로_한_것: dict[str, str] = {}


def 웹_파일들() -> list[Path]:
    return sorted(
        p
        for d in ("web/lib", "web/app", "web/components", "web/__tests__")
        for p in (뿌리 / d).rglob("*")
        if p.is_file() and p.suffix in (".ts", ".tsx") and "node_modules" not in p.parts
    )


def 아무도_안_쓰는_웹_이름() -> list[str]:
    """`web/lib` 이 내보내는 이름 중 **어디에서도 안 쓰이는** 것.

    파이썬 쪽과 같은 잣대다 — 정의 말고 한 번이라도 더 나오면 쓰이는 것으로 본다.
    같은 파일 안에서만 쓰는 것도 쓰는 것이다(SQL 상수가 대개 그렇다).
    """
    본문 = {p: p.read_text(encoding="utf-8") for p in 웹_파일들()}
    전체 = " ".join(본문.values()) + " " + " ".join(
        p.read_text(encoding="utf-8")
        for d in ("docs", ".github", "scripts")
        for p in (뿌리 / d).rglob("*")
        if p.is_file() and p.suffix in (".md", ".yml", ".py")
    )
    안쓰임 = []
    for 경로, 소스 in 본문.items():
        if 경로.parent.name != "lib" or "lib" not in 경로.parts:
            continue
        # **한글 이름도 센다.** 처음에 `[A-Za-z_]` 로만 잡았다가, 일부러 심은
        # `export const 시험용_죽은값` 을 **못 잡고 통과했다** (2026-09-23)
        for m in re.finditer(r"^export (?:async )?(?:function|const) ([A-Za-z_가-힣][\w가-힣]*)", 소스, re.M):
            이름 = m.group(1)
            if 이름 in 웹_두기로_한_것:
                continue
            if len(re.findall(rf"\b{re.escape(이름)}\b", 전체)) <= 1:
                줄 = 소스[: m.start()].count("\n") + 1
                안쓰임.append(f"{경로.relative_to(뿌리)}:{줄}  {이름}")
    return sorted(안쓰임)


def test_웹도_훑을_것이_있다() -> None:
    """훑기가 조용히 0개를 내면 아래가 무조건 통과한다."""
    파일 = 웹_파일들()

    assert len(파일) > 60, f"{len(파일)}개만 훑었다"
    assert any(p.name == "db.ts" for p in 파일)


def test_웹에_죽은_내보내기가_늘지_않았다() -> None:
    죽은것 = 아무도_안_쓰는_웹_이름()

    assert not 죽은것, (
        "web/lib 이 내보내는데 아무도 쓰지 않는 이름이 생겼다. 지우거나, 쓰는 곳에"
        " 연결하거나, 남길 이유를 `웹_두기로_한_것` 에 적는다 (docs/infra.md 25.153):\n  "
        + "\n  ".join(죽은것)
    )


def test_시험용_문을_다시_내지_않는다() -> None:
    """`export const __internal = {...}` — 테스트가 안쪽 함수를 보려고 낸 문이다.

    정작 그 문을 **아무 테스트도 쓰지 않았다**(2026-09-23 확인). 안쪽 함수들은
    같은 파일 안에서 쓰이고 있으니 문만 지우면 된다. 다시 내고 싶어지면
    **먼저 쓰는 테스트를 쓴다** — 쓰지 않는 문은 "여기로 들어와도 된다" 는 거짓 신호다.
    """
    소스 = (뿌리 / "web" / "lib" / "db.ts").read_text(encoding="utf-8")

    assert "__internal" not in 소스
