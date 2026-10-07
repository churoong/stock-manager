"""포트폴리오 **상한 두 개**가 뒤바뀌지 않는지 (docs/infra.md 25.67).

CLAUDE.md 매매 규칙: **비중 상한 기본 10%, 단일 섹터 상한 30%.**
둘 다 실수(float)이고, `report_picks.compose()`·`build_portfolio()` 의 긴 인자 목록에
따로 떨어져 있다. 자리로 넘기다 뒤바뀌면

- 타입이 같아 파이썬이 아무 말도 안 한다
- 두 상한을 기본값으로 재는 기존 테스트도 그대로 통과한다
- 결과는 **한 종목이 포트폴리오의 30% 를 가져가는 추천**이다

값이 잘못된 추천은 조용히 나가고, 그것을 보고 돈을 넣는다. 그래서 이 자리는
**이름을 붙여 넘긴다.** 이 파일이 그것을 지킨다.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from batch.services import report_picks as rp

뿌리 = Path(__file__).resolve().parent.parent

#: 이름을 붙여 넘겨야 하는 함수와, 자리로 넘겨도 되는 앞쪽 인자 수.
#: 앞쪽 셋(rows·total_investable·signals_as_of)은 뜻이 서로 달라 헷갈릴 일이 없다.
지킬함수 = {"compose": 3, "build_portfolio": 2}


def 호출들() -> list[tuple[str, int, str, int]]:
    """(파일, 줄, 함수이름, 위치 인자 수)."""
    나온것 = []
    for 길 in sorted((뿌리 / "batch").rglob("*.py")):
        if "__pycache__" in str(길):
            continue
        for 마디 in ast.walk(ast.parse(길.read_text(encoding="utf-8"))):
            if not isinstance(마디, ast.Call):
                continue
            이름 = 마디.func.attr if isinstance(마디.func, ast.Attribute) else getattr(마디.func, "id", "")
            if 이름 in 지킬함수:
                나온것.append((str(길.relative_to(뿌리)), 마디.lineno, 이름, len(마디.args)))
    return 나온것


def test_부르는_곳을_찾아냈다() -> None:
    """0개면 아래가 공짜로 통과한다."""
    찾음 = 호출들()

    assert len(찾음) >= 2
    assert {이름 for _, _, 이름, _ in 찾음} == set(지킬함수)


@pytest.mark.parametrize("호출", 호출들(), ids=lambda c: f"{c[0]}:{c[1]} {c[2]}")
def test_상한은_이름을_붙여_넘긴다(호출: tuple[str, int, str, int]) -> None:
    파일, 줄, 이름, 위치수 = 호출

    assert 위치수 <= 지킬함수[이름], (
        f"{파일}:{줄} 가 {이름}() 에 위치 인자 {위치수}개를 넘긴다.\n"
        "max_stock_pct(10%)와 max_sector_pct(30%)는 둘 다 float 이라 뒤바뀌어도"
        " 아무도 모른다. 이름을 붙여라"
    )


class Test상한이_실제로_다르게_동작한다:
    """이름을 붙이는 것이 **왜** 중요한지 — 두 값이 서로 다른 일을 한다."""

    def 신호(self, n: int) -> list[rp.SignalRow]:
        칸 = inspect.signature(rp.SignalRow).parameters
        나온것 = []
        for i in range(n):
            값: dict = {}
            for 이름, 인자 in 칸.items():
                if 인자.default is not inspect.Parameter.empty:
                    continue
                값[이름] = {
                    "stock_id": i + 1, "ticker": f"00{i}", "name": f"종목{i}",
                    "horizon": "mid", "total_score": 90 - i, "weight_pct": 100.0,
                }.get(이름, 0)  # fmt: skip
            나온것.append(rp.SignalRow(**값))
        return 나온것

    def test_기본값이_CLAUDE_md_와_같다(self) -> None:
        assert rp.DEFAULT_MAX_STOCK_PCT == 10.0
        assert rp.DEFAULT_MAX_SECTOR_PCT == 30.0

    def test_종목_상한이_섹터_상한보다_작다(self) -> None:
        """뒤바뀌면 이 관계가 뒤집힌다. 한 종목이 섹터보다 많이 가져갈 수는 없다."""
        assert rp.DEFAULT_MAX_STOCK_PCT < rp.DEFAULT_MAX_SECTOR_PCT

    def test_서명의_자리도_지켜본다(self) -> None:
        """이름을 붙여 부르므로 자리가 바뀌어도 되지만, **둘이 붙어 있으면** 위험하다."""
        칸 = list(inspect.signature(rp.compose).parameters)

        assert "max_sector_pct" in 칸 and "max_stock_pct" in 칸
        assert abs(칸.index("max_sector_pct") - 칸.index("max_stock_pct")) > 1, (
            "두 상한이 서명에서 이웃하면 자리로 넘기던 시절의 실수가 더 쉬워진다"
        )
