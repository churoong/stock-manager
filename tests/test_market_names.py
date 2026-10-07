"""**시장 이름이 세 곳에 손으로 적혀 있었다** (docs/infra.md 25.184).

`stocks.market` 에 들어가는 미국 시장 이름의 정의처는 하나다 —
`batch/sources/nasdaq_symbols.EXCHANGE_CODES`(+ 나스닥 파일의 `default_exchange`).
`jobs/universe` 가 `s.exchange` 를 그대로 `stocks.market` 에 넣는다.

그 이름으로 **판단하는** 곳이 둘 더 있었고, 둘 다 손으로 적은 목록이었다.

* `services/trend.MARKET_INDEX` — 시장 → 국면을 볼 지수. `NYSE`·`NASDAQ`·`AMEX` 셋뿐이라
  `NYSE American`·`NYSE Arca`·`Cboe BZX`·`IEX` 종목은 **국면을 영영 모른다.**
  약세장에서 비중이 안 줄고, 근거표에는 "국면을 몰라 줄이지 않았습니다" 가 찍힌다 —
  같은 실행에서 SP500 국면은 이미 계산돼 있는데도. `AMEX` 는 아무도 안 만드는 죽은 키였다
* `web/lib/screener.MARKETS_BY_COUNTRY` — 시장 필터 목록. 셋뿐이라 나머지 종목은
  **고를 수가 없었다**

`docs/factors.md` 가 실측을 적어 둔다 — Cboe BZX 4종목, NYSE Arca 12종목. 있는 데이터다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from batch.services import trend
from batch.sources import nasdaq_symbols

뿌리 = Path(__file__).resolve().parent.parent


def 웹_시장_목록() -> list[str]:
    """`MARKETS_BY_COUNTRY.US` 의 값들. `ALL` 은 뺀다."""
    글 = (뿌리 / "web" / "lib" / "screener.ts").read_text(encoding="utf-8")
    본문 = 글[글.index("export const MARKETS_BY_COUNTRY") :]
    본문 = 본문[본문.index("US: [") : 본문.index("} as const;")]
    return [이름 for 이름, _ in re.findall(r'\["([^"]+)",\s*"([^"]+)"\]', 본문) if 이름 != "ALL"]


def test_읽어_냈다() -> None:
    """훑기가 조용히 비면 아래가 공짜로 통과한다."""
    assert len(nasdaq_symbols.US_MARKETS) >= 6, nasdaq_symbols.US_MARKETS
    assert len(웹_시장_목록()) >= 6, 웹_시장_목록()


def test_정의처가_실제_원본에서_나온다() -> None:
    """손으로 적으면 원본이 바뀌어도 모른다."""
    assert set(nasdaq_symbols.EXCHANGE_CODES.values()) <= set(nasdaq_symbols.US_MARKETS)
    assert nasdaq_symbols.FILES["nasdaqlisted.txt"]["default_exchange"] in nasdaq_symbols.US_MARKETS


@pytest.mark.parametrize("시장", nasdaq_symbols.US_MARKETS)
def test_모든_미국_시장이_지수를_찾는다(시장: str) -> None:
    """**국면을 모르는 시장이 있으면 그 종목만 비중이 안 줄어든다.**

    docs/signals.md 3.5 표는 지수를 "미국 S&P 500(전 시장)" 이라고 약속한다.
    """
    assert trend.index_for_market(시장) == "SP500", (
        f"`{시장}` 종목은 시장 국면을 못 찾는다 — 약세장에서도 비중이 안 줄고,"
        " 근거표는 '국면을 몰라 줄이지 않았습니다' 라고 적는다"
    )


def test_국내도_빠짐없이_찾는다() -> None:
    for 시장 in ("KOSPI", "KOSDAQ"):
        assert trend.index_for_market(시장) is not None


def test_죽은_키가_없다() -> None:
    """`AMEX` 처럼 아무도 안 만드는 이름이 남아 있으면 **틀려도 모른다.**"""
    아는것 = {"KOSPI", "KOSDAQ", *(이름.upper() for 이름 in nasdaq_symbols.US_MARKETS)}
    죽은것 = sorted(set(trend.MARKET_INDEX) - 아는것)

    assert not 죽은것, (
        f"`MARKET_INDEX` 에 아무도 만들지 않는 시장 이름이 있다: {죽은것}\n"
        "쓰지 않는 상수는 틀려도 아무도 모른다"
    )


def test_웹_시장_필터가_같은_목록을_쓴다() -> None:
    """빠진 시장의 종목은 **화면에서 고를 수가 없다.**"""
    빠진것 = sorted(set(nasdaq_symbols.US_MARKETS) - set(웹_시장_목록()))

    assert not 빠진것, (
        f"web/lib/screener.MARKETS_BY_COUNTRY.US 에 없는 시장: {빠진것}\n"
        "그 시장 종목은 시장 필터로 고를 수 없다"
    )


def test_웹에_없는_시장을_적지_않았다() -> None:
    남는것 = sorted(set(웹_시장_목록()) - set(nasdaq_symbols.US_MARKETS))

    assert not 남는것, f"화면에만 있는 시장 이름: {남는것} — 고르면 0건이 나온다"


def test_대소문자를_가리지_않는다() -> None:
    """`stocks.market` 이 원본 표기 그대로라 `index_for_market` 은 `upper()` 로 본다."""
    assert trend.index_for_market("nyse arca") == "SP500"
    assert trend.index_for_market(None) is None
    assert trend.index_for_market("") is None
