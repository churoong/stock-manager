"""근거표의 **단일 정의처** — 한 행이 화면까지 가는가, 그리고 근거 없는 추천을 어떻게 다루나.

CLAUDE.md 절대 규칙 (2026-09-16 사용자 확정):

> 모든 추천은 왜 추천했는지가 분명해야 하고, 사용자가 확인할 수 있어야 한다. …
> **근거표를 만들 수 없는 추천은 표시하지 않는다**

이 판정은 오래 `services/signals.py` 안에만 있었다. 그런데 추천을 내는 곳은 넷이다 —
**종목 신호·ETF 핵심·ETF 위성·적립 종목.** 규칙을 지키는 장치가 한 곳에만 있으면
나머지 셋은 규칙 밖에서 돈다(docs/infra.md 25.174). 그래서 판정을 여기로 옮기고
`signals.py` 는 이 모듈을 쓴다. **정의는 한 곳이다.**

여기에는 다른 모듈을 import 하지 않는다. 넷 모두가 부를 수 있어야 하기 때문이다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeVar

#: 근거표 한 행이 화면에 그려지려면 이 넷이 **문자열**이어야 한다.
#: 웹의 `parseCriteria()`(web/lib/recommend.ts)가 조건에 안 맞는 행을 **조용히 버린다** —
#: 오류도 로그도 없이 사라진다. 그래서 여기서 같은 조건을 두고 미리 거른다.
#: 두 곳이 어긋나지 않게 `tests/test_criteria_contract.py` 가 웹 소스를 읽어 대조한다.
CRITERION_REQUIRED_KEYS = ("label", "display", "threshold", "source")

#: 근거를 못 만들어 추천에서 뺐을 때 남기는 사유. 화면의 "빠진 목록" 에 그대로 뜬다.
#: **0건이어야 정상이다.** 이 사유가 보이면 근거표를 만드는 쪽이 고장 난 것이다
NO_CRITERIA_REASON = "근거표를 만들지 못했습니다 (확인할 수 없는 추천은 내보내지 않습니다)"


def displayable(row: dict) -> bool:
    """이 근거 행이 화면까지 가는가. 웹의 거르개와 같은 판정이다."""
    # dict 가 아니면(문자열·숫자) 그릴 수 없다 — `.get` 이 AttributeError 로 리포트 전체를
    # 죽이지 않게 (25.822, 교차검증)
    if not isinstance(row, dict) or not row:
        return False
    return all(isinstance(row.get(key), str) for key in CRITERION_REQUIRED_KEYS)


def usable(rows: Any) -> bool:
    """**확인할 수 있는 추천인가.** 화면까지 가는 행이 한 줄이라도 있어야 한다.

    화면에서 막는 길도 있지만, 근거가 없는 추천은 **애초에 내보내지 않는 쪽**이 맞다 —
    화면이 넷이고 하나라도 빠뜨리면 규칙이 샌다.
    """
    return isinstance(rows, list) and bool(rows) and any(displayable(row) for row in rows)


class 후보(Protocol):
    """ETF 판정(`services/etf.Evaluation`)과 적립 판정(`services/accumulation.Judgement`)의 공통 모양."""

    passed: bool
    excluded_reason: str | None
    criteria: list[dict]


#: PEP695 문법(`def f[T: 후보]`)을 안 쓴다 — 실행 환경이 파이썬 3.11 이라 문법 오류가 난다
T = TypeVar("T", bound=후보)


def drop_unverifiable(items: list[T], name: Callable[[T], str]) -> list[str]:  # noqa: UP047 — 위 주석
    """`passed` 인데 근거표를 만들 수 없는 것을 **추천에서 뺀다.** 뺀 것의 이름을 돌려준다.

    **지우지 않고 떨어뜨린다.** 행 자체는 남겨야 "왜 안 나왔나" 에 답할 수 있다
    (ETF·적립은 떨어진 것도 사유와 함께 화면에 보여 준다). 종목 신호 쪽은 통과한 것만
    저장하는 구조라 거기서는 내보내지 않는 것으로 같은 일을 한다.

    **순위를 매기기 전에 부른다.** 나중에 부르면 뺀 것이 백분위와 집단 크기를 이미
    흔들어 놓은 뒤다 — 남은 것들의 순위가 있지도 않은 후보를 세고 만들어진다.
    """
    뺀것: list[str] = []
    for item in items:
        if item.passed and not usable(item.criteria):
            item.passed = False
            item.excluded_reason = NO_CRITERIA_REASON
            뺀것.append(name(item))
    return 뺀것
