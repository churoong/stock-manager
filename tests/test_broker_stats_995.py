"""증권사 적중률 성적표 (docs/brokers.md, docs/infra.md 25.995) — 손으로 셀 수 있는 고정 데이터."""

from __future__ import annotations

from datetime import date, timedelta

from batch.services import broker_stats as bs


def _days(n: int) -> list[str]:
    d, out = date(2026, 1, 1), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


DAYS = _days(200)


def _bars(closes: list[float], highs: list[float] | None = None, ratio: list[float] | None = None) -> list[bs.Bar]:
    return [bs.Bar(DAYS[i], c, (highs or closes)[i], (ratio or [1.0] * len(closes))[i]) for i, c in enumerate(closes)]


def test_기준가는_다음_거래일_초과수익은_지수를_뺀다() -> None:
    # 발표 DAYS[0] → 기준가 DAYS[1] = 100. 60거래일 뒤 DAYS[61] = 120 (+20%). 지수는 같은 기간 +5% → 초과 +15%p
    closes = [90.0] + [100.0] * 60 + [120.0] * 139
    idx = [1000.0] + [1000.0] * 60 + [1050.0] * 139
    rows = bs.compute([bs.Opinion(1, "KOSPI", DAYS[0], "가증권", 150.0)], {1: _bars(closes)}, {"KOSPI": _bars(idx)})
    [r] = rows
    assert r["avg_upside_pct"] == 50.0 and r["n_fwd"] == 1 and r["avg_excess_pct"] == 15.0 and r["hit_pct"] == 100.0
    # 120거래일 안에 고가가 150 에 못 닿았고 120거래일이 다 지났다 → 터치 0%
    assert r["n_touch"] == 1 and r["touch_pct"] == 0.0


def test_120일이_덜_찼으면_닿았어도_안_센다() -> None:
    """덜 찬 의견 가운데 닿은 것만 세면 터치율이 부풀었다 (docs/infra.md 25.1148)."""
    highs = [100.0] * 10 + [151.0] + [100.0] * 9
    rows = bs.compute([bs.Opinion(1, "KOSPI", DAYS[0], "나", 150.0), bs.Opinion(2, "KOSPI", DAYS[0], "다", 150.0)],
                      {1: _bars([100.0] * 20, highs), 2: _bars([100.0] * 20)}, {"KOSPI": _bars([1.0] * 20)})  # fmt: skip
    by = {r["broker"]: r for r in rows}
    assert by["나"]["n_touch"] == 0 and by["나"]["touch_pct"] is None
    assert by["다"]["n_touch"] == 0 and by["다"]["n_fwd"] == 0  # 60거래일도 안 지났다


def test_기간_안_분할이면_뺀다() -> None:
    ratio = [1.0] * 30 + [0.2] * 170  # 1:5 분할
    rows = bs.compute([bs.Opinion(1, "KOSPI", DAYS[0], "라", 150.0)],
                      {1: _bars([100.0] * 200, ratio=ratio)}, {"KOSPI": _bars([1.0] * 200)})  # fmt: skip
    assert rows[0]["skipped_action"] == 1 and rows[0]["n_target"] == 0


def test_표본이_적으면_성적을_말하지_않는다() -> None:
    assert bs.line({"n_fwd": 5}).startswith("표본 부족")
    ok = {"n_fwd": 30, "avg_excess_pct": 1.24, "hit_pct": 54.2, "n_touch": 10, "touch_pct": 30.0, "avg_upside_pct": 38.4}
    assert bs.line(ok) == "60거래일 초과수익 평균 +1.2%p · 맞힘 54% (30건) · 목표가 터치 30% (10건) · 제시 상승여력 평균 +38%"


def test_다_찬_창에서_닿으면_센다() -> None:
    highs = [100.0] * 10 + [151.0] + [100.0] * 189
    rows = bs.compute([bs.Opinion(1, "KOSPI", DAYS[0], "마", 150.0)],
                      {1: _bars([100.0] * 200, highs)}, {"KOSPI": _bars([1.0] * 200)})  # fmt: skip
    assert rows[0]["n_touch"] == 1 and rows[0]["touch_pct"] == 100.0


def test_시세가_끊긴_종목은_마지막_종가로_창을_채운다() -> None:
    """−70% 로 떨어진 뒤 상장폐지된 종목이 조용히 빠져 증권사가 더 잘 맞힌 것처럼 보였다 (docs/infra.md 25.1155)."""
    끊긴 = [100.0] * 10 + [30.0] * 20  # 30거래일 뒤 시세 없음
    rows = bs.compute(
        [bs.Opinion(1, "KOSPI", DAYS[0], "바", 150.0)],
        {1: _bars(끊긴), 2: _bars([50.0] * 200)},  # 2번이 시세 전체의 마지막 날을 정한다
        {"KOSPI": _bars([1000.0] * 200)},
    )
    [r] = rows
    assert r["n_fwd"] == 1 and r["avg_excess_pct"] == -70.0 and r["hit_pct"] == 0.0
    assert r["n_touch"] == 1 and r["touch_pct"] == 0.0


def test_끊긴_종목도_시장_창이_덜_지났으면_안_센다() -> None:
    rows = bs.compute(
        [bs.Opinion(1, "KOSPI", DAYS[0], "사", 150.0)],
        {1: _bars([100.0] * 10), 2: _bars([50.0] * 40)},  # 시장은 40거래일만 지났다 — 60일 창이 안 찼다
        {"KOSPI": _bars([1000.0] * 40)},
    )
    assert rows[0]["n_fwd"] == 0 and rows[0]["n_touch"] == 0
