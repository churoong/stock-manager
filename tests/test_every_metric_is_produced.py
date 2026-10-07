"""**등록된 지표는 두 경로가 실제로 값을 만드는가** (docs/infra.md 25.92).

`docs/infra.md` 25.65 는 베타가 배선 하나 때문에 한 번도 계산된 적이 없었던 사고다.
그 뒤 `tests/test_scoring_new_metrics.py` 에 "정의만 하고 **등록**을 잊지 않았나" 를
보는 시험을 두었다. 그런데 그 시험은 **한 방향만** 본다.

2026-09-21 에 반대 방향으로 같은 사고가 났다 — `idio_volatility` 를 `FACTOR_METRICS` 에
**등록만** 하고 값을 만드는 곳을 안 붙였다. 등록 시험은 초록이고 배치도 조용했다.
그 사이

  · `factor_from_metrics` 의 **분모**가 8에서 10이 되어 리스크 문턱이 4→5 로 올랐다.
    실제로 늘어난 재료는 하나뿐인데 문턱은 올라, 재료가 성긴 종목이 점수를 잃을 수 있었다
  · 모든 종목·모든 기준일의 `missing_fields` 에 그 이름이 영구히 들어가,
    화면이 "빠짐: 잔차 변동성" 을 영원히 찍었다 — 사용자는 수집이 덜 된 줄 안다

그래서 **양방향**으로 본다. 등록된 이름은 반드시 두 경로가 만들어야 한다.
"""

from __future__ import annotations

import pytest

from batch.jobs import backtest as bj
from batch.jobs import scores as sj
from batch.services import backtest as bt
from batch.services import scoring as sc

#: 넉넉한 계열. 계열 지표가 전부 계산되는 길이
_DAYS = max(sc.MOMENTUM_OFFSETS) + 2


def _날짜들(n: int) -> list[str]:
    from datetime import date, timedelta

    기준 = date(2024, 1, 1)
    return [(기준 + timedelta(days=i)).isoformat() for i in range(n)]


def _오르내리는(n: int) -> list[float]:
    값, 나온것 = 100.0, []
    for i in range(n):
        값 *= 1.01 if i % 2 == 0 else 0.995
        나온것.append(값)
    return 나온것


날짜 = _날짜들(_DAYS)
종가 = _오르내리는(_DAYS)
지수 = [1000.0 * (1 + 0.004 * (1 if i % 2 == 0 else -1)) ** 1 for i in range(_DAYS)]
지수계열 = dict(zip(날짜, [1000.0 * (1.003 if i % 2 == 0 else 0.998) ** (i + 1) for i in range(_DAYS)], strict=True))

재무 = {
    "net_income": 100.0, "total_assets": 1000.0, "total_equity": 500.0,
    "operating_income": 120.0, "revenue": 800.0, "total_liabilities": 400.0,
    "current_assets": 300.0, "current_liabilities": 150.0, "noncurrent_liabilities": 100.0,
}


def 적재가_만드는_키() -> set[str]:
    묶음 = sj.build_inputs(
        [{"stock_id": 1, "market": "KOSPI", "sector": None, "market_cap": 1_000_000.0}],
        {1: {2025: dict(재무), 2024: dict(재무), 2022: dict(재무)}},
        {1: {"mdd": -0.3, "volatility_ann": 0.25, "sharpe": 1.0, "sortino": 1.2,
             "beta": 1.1, "mdd_recovery_days": 30, "cagr": 0.12}},
        # 모멘텀도 **이 계열 하나**에서 나온다 (2026-09-21, docs/infra.md 25.100).
        # 예전에는 날짜로 고른 기준점을 따로 넘겼고, 그래서 이 시험 자료는
        # 두 경로가 **구조상 반드시 일치**하게 만들어져 있었다 — 갈라짐을 못 봤다
        {1: (날짜, 종가, [1_000_000_000.0] * _DAYS)},
        dividends={1: 50_000.0},
        benchmark_closes=지수계열,
    )
    return set(묶음[0].metrics)


def 백테스트가_만드는_키() -> set[str]:
    view = bt.PriceView({1: dict(zip(날짜, 종가, strict=True))}, 날짜[-1],
                        turnover={1: dict(zip(날짜, [1_000_000_000.0] * _DAYS, strict=True))})
    스냅 = [{"fiscal_year": y, "as_of_date": "2020-01-01", "values": dict(재무)} for y in (2022, 2024, 2025)]
    묶음 = bj.build_pit_inputs(
        [{"stock_id": 1, "market": "KOSPI", "sector": None, "listed_shares": 1000}],
        {1: 스냅}, view,
        {1: [("2020-01-01", 2025, 50_000.0)]},
        지수계열,
    )
    return set(묶음[0].metrics)


등록된 = {m.name for ms in sc.FACTOR_METRICS.values() for m in ms}


def test_읽어_냈다() -> None:
    """0개면 아래가 공짜로 통과한다."""
    assert len(등록된) >= 25
    assert 적재가_만드는_키() and 백테스트가_만드는_키()


@pytest.mark.parametrize("지표", sorted(등록된))
def test_적재_경로가_그_지표를_만든다(지표: str) -> None:
    assert 지표 in 적재가_만드는_키(), (
        f"`{지표}` 가 FACTOR_METRICS 에 등록돼 있는데 `jobs/scores.build_inputs` 가 만들지 않는다.\n"
        "그러면 **분모에만 세어져** 절반 규칙의 문턱을 올리고, 모든 종목의 missing_fields 에\n"
        "영구히 들어가 화면이 '빠짐' 을 영원히 찍는다 (docs/infra.md 25.92).\n"
        "값을 만들 수 없다면 **등록하지 마라** — 계산 함수만 두면 된다"
    )


@pytest.mark.parametrize("지표", sorted(등록된))
def test_백테스트_경로가_그_지표를_만든다(지표: str) -> None:
    assert 지표 in 백테스트가_만드는_키(), (
        f"`{지표}` 를 `jobs/backtest.build_pit_inputs` 가 만들지 않는다.\n"
        "적재와 백테스트의 계산식은 하나여야 한다(docs/factors.md 10.1·11.6).\n"
        "갈라지면 백테스트가 **운영이 쓰지 않는 규칙**의 성적표가 되고,\n"
        "'넣기 전·후 비교' 로 그 지표를 검증하는 길이 막힌다"
    )


def test_두_경로가_같은_지표를_만든다() -> None:
    """한쪽에만 있는 키는 **조용한 갈라짐**이다."""
    적재, 백 = 적재가_만드는_키(), 백테스트가_만드는_키()

    assert 적재 - 백 == set(), f"적재에만 있다: {sorted(적재 - 백)}"
    assert 백 - 적재 == set(), f"백테스트에만 있다: {sorted(백 - 적재)}"


def test_등록된_지표에_한글_이름이_있다() -> None:
    """근거표에 설명할 수 없는 지표는 넣으면 안 된다 (CLAUDE.md)."""
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent / "web" / "lib" / "stockDetail.ts").read_text(
        encoding="utf-8"
    )
    없는것 = [이름 for 이름 in sorted(등록된) if f"{이름}:" not in 글]

    assert not 없는것, f"web/lib/stockDetail.ts 의 METRIC_LABELS 에 없다: {없는것}"
