"""백테스트 리스크 팩터가 운영과 같은 지표를 낸다 (docs/infra.md 25.622, 감사)."""

from __future__ import annotations

import math
from datetime import date, timedelta

from batch.jobs import backtest as job


def _계열(n: int, 기울기: float, 흔들림: float) -> list[tuple[str, float]]:
    시작 = date(2023, 1, 2)
    return [((시작 + timedelta(days=i)).isoformat(), 100 * (1 + 기울기) ** i * (1 + 흔들림 * math.sin(i))) for i in range(n)]


def test_지수와_무위험수익률을_주면_샤프와_베타가_나온다() -> None:
    종목 = _계열(800, 0.0004, 0.01)
    지수 = dict(_계열(800, 0.0003, 0.008))
    예전 = job.risk_from_prices(종목)
    지금 = job.risk_from_prices(종목, 지수, 0.03)
    빈칸 = [k for k, v in 예전.items() if v is None]
    assert 빈칸, "지수·금리 없이는 비는 지표가 있어야 비교가 된다"
    assert all(지금[k] is not None for k in 빈칸), f"여전히 빈 지표: {[k for k in 빈칸 if 지금[k] is None]}"


def test_시점_입력이_지수와_금리를_넘긴다() -> None:
    import inspect

    src = inspect.getsource(job)
    assert "closes, risk_free_annual=_RISK_FREE, benchmark_points=지수_점," in src
    assert "until=date.fromisoformat(str(view.broad_last() or cutoff)[:10])" in src  # 25.717·25.723 — 창 끝
    assert "_RISK_FREE = metrics_job.risk_free_for(client, country)" in src


def test_미리_읽은_지수_점과_dict_가_같은_값을_낸다() -> None:
    """성능을 위해 지수를 한 번만 읽게 바꿨다(25.625) — 값은 그대로여야 한다."""
    from batch.services import metrics as m

    종목 = _계열(800, 0.0004, 0.01)
    지수 = dict(_계열(900, 0.0003, 0.008))
    점 = [m.PricePoint(date=date.fromisoformat(d), close=c) for d, c in sorted(지수.items())]
    assert job.risk_from_prices(종목, 지수, 0.03) == job.risk_from_prices(종목, risk_free_annual=0.03, benchmark_points=점)


def test_리스크_지표_캐시는_다른_가격을_섞지_않는다() -> None:
    """같은 종목·기준일이라도 가격이 다르면 다시 계산한다 (25.625)."""
    import inspect

    src = inspect.getsource(job.build_pit_inputs)
    assert "len(closes), closes[0][0], closes[-1][1]" in src
    assert "_위험_캐시.clear()" in inspect.getsource(job)


def test_상장_3년이_안_되면_운영처럼_1년_창으로_내려간다() -> None:
    """백테스트는 마지막 750행을 늘 3Y 로 계산해 운영(창 커버리지 → 1Y)과 갈라졌다 (docs/infra.md 25.710, 교차검증)."""
    from datetime import date, timedelta

    from batch.jobs import backtest as bt_job
    from batch.services import metrics as m

    시작 = date(2024, 1, 1)
    값 = [100.0 * (1 + 0.01 * ((i % 7) - 3)) for i in range(610)]
    closes = [((시작 + timedelta(days=i)).isoformat(), v) for i, v in enumerate(값)]
    결과 = bt_job.risk_from_prices(closes)
    끝 = 시작 + timedelta(days=609)
    일년 = [m.PricePoint(date.fromisoformat(d), c) for d, c in closes if date.fromisoformat(d) >= 끝 - timedelta(days=366)]
    기대 = m.compute("1Y", 일년)
    assert 결과["mdd_abs"] == abs(기대.mdd)  # type: ignore[arg-type]
    assert 결과["volatility_ann"] == 기대.volatility_ann


def test_시세가_cutoff_보다_한참_먼저_끊겼으면_값을_내지_않는다() -> None:
    """운영은 창 끝이 비면 None 인데 백테스트는 종목의 마지막 시세를 끝으로 잡아 값을 냈다 (docs/infra.md 25.717, 교차검증)."""
    from datetime import date, timedelta

    from batch.jobs import backtest as bt_job

    시작 = date(2024, 1, 1)
    closes = [((시작 + timedelta(days=i)).isoformat(), 100.0 * (1 + 0.01 * ((i % 7) - 3))) for i in range(400)]
    끝 = 시작 + timedelta(days=399)
    assert bt_job.risk_from_prices(closes, until=끝)["mdd_abs"] is not None
    assert bt_job.risk_from_prices(closes, until=끝 + timedelta(days=60))["mdd_abs"] is None


def test_한_종목만_있는_날이_cutoff_여도_창_끝은_대부분이_들어온_날이다() -> None:
    """합집합 날짜 축에서 한 종목만 시세가 있는 날이 cutoff 면 나머지 전부의 리스크가 None 이었다 (docs/infra.md 25.723, 교차검증 재현)."""
    from batch.services import backtest as bt

    가격 = {sid: {f"2026-08-{d:02d}": 100.0 for d in range(1, 20)} for sid in range(1, 5)}
    가격[1]["2026-09-05"] = 101.0  # 1번만 구멍 기간에 시세가 있다
    view = bt.PriceView(가격, "2026-09-05")
    assert view.broad_last() == "2026-08-19"
