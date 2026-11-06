"""ETF 전술 가상계좌 판정 `etf_tactical_v1` (docs/paper.md 17장, 2026-11-06 사용자 지시) — 손으로 셀 수 있는 지수."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from batch.services import etf_tactical as et


def _줄(값: list[float], 첫날: date = date(2025, 1, 1)) -> list[tuple[str, float]]:
    return [((첫날 + timedelta(days=i)).isoformat(), v) for i, v in enumerate(값)]


def test_200일치_미만이면_모름이고_현금() -> None:
    s = _줄([100.0] * 199)
    r = et.regime(s, s[-1][0])
    assert r.state == et.UNKNOWN and et.target(r.state) == {}


def test_오르면_상승_지수_ETF_98() -> None:
    s = _줄([100.0 + i for i in range(220)])  # 종가 319 ≥ 200일선(219.5), 20일 수익 319/299−1 > 0
    r = et.regime(s, s[-1][0])
    assert r.state == et.UP and r.sma == pytest.approx(sum(range(120, 320)) / 200)
    assert r.mom == pytest.approx(319 / 299 - 1) and et.target(r.state) == {"long": 0.98}


def test_내리면_하락_인버스_50() -> None:
    s = _줄([400.0 - i for i in range(220)])
    r = et.regime(s, s[-1][0])
    assert r.state == et.DOWN and et.target(r.state) == {"inverse": 0.5}


def test_200일선_위인데_20일_수익이_음수면_엇갈림_현금() -> None:
    s = _줄([100.0 + i for i in range(200)] + [299.0 - i for i in range(21)])  # 마지막 279 > 200일선, 20일 전 299
    r = et.regime(s, s[-1][0])
    assert r.close > r.sma and r.mom < 0 and r.state == et.MIXED and et.target(r.state) == {}


def test_판정일_뒤_값은_보지_않고_묵으면_모름() -> None:
    s = _줄([100.0 + i for i in range(220)])
    # 판정일을 210번째 날로 — 그 뒤 값을 쓰면 종가가 달라진다
    r = et.regime(s, s[209][0])
    assert r.as_of == s[209][0] and r.close == 309.0
    # 마지막 값보다 8일 뒤 판정 — 묵음
    늦음 = (date.fromisoformat(s[-1][0]) + timedelta(days=8)).isoformat()
    assert et.regime(s, 늦음).state == et.UNKNOWN
    assert et.regime(s, (date.fromisoformat(s[-1][0]) + timedelta(days=7)).isoformat()).state == et.UP


def test_리플레이는_다음_날부터_들고_한_번_갈아탄_비용을_낸다() -> None:
    s = _줄([100.0 + i for i in range(203)])  # 200번째 날(인덱스 200, 종가 300) 첫 판정 상승 → 201·202 날 수익을 든다
    r = et.replay(s, cost=0.001)
    기대 = (1 - 0.001 * 0.98) * (1 + 0.98 * (301 / 300 - 1)) * (1 + 0.98 * (302 / 301 - 1))
    assert r["switches"] == 1 and r["days"] == 2
    assert r["twr_pct"] == pytest.approx((기대 - 1) * 100)
    assert r["hold_pct"] == pytest.approx((302 / 300 - 1) * 100)


def test_평평하면_엇갈림_현금이라_수익_0() -> None:
    r = et.replay(_줄([100.0] * 230))
    assert r["switches"] == 0 and r["twr_pct"] == 0.0 and r["hold_pct"] == 0.0


def test_mdd_손으로() -> None:
    assert et._mdd([1.0, 1.2, 0.9, 1.0]) == pytest.approx(-0.25)


def test_계좌_둘은_시장과_지수가_다르다() -> None:
    assert et.ACCOUNTS["X"]["market"] == "KR" and et.ACCOUNTS["X"]["index"] == "KOSPI"
    assert et.ACCOUNTS["Y"]["market"] == "US" and et.ACCOUNTS["Y"]["index"] == "SP500"
    from batch.jobs import etf_link

    # 엔진이 찾는 ETF 몫이 잇기 목록과 같다
    assert {et.ACCOUNTS["X"]["long"], et.ACCOUNTS["X"]["inverse"]} == set(etf_link.TACTICAL_ETFS["KR"]["names"])
    assert {et.ACCOUNTS["Y"]["long"], et.ACCOUNTS["Y"]["inverse"]} == set(etf_link.TACTICAL_ETFS["US"]["symbols"])


def test_갈아탈_때는_오늘_팔고_다음_날_산다() -> None:
    assert et.step(None, None) == ("hold", None)
    assert et.step("long", "long") == ("hold", "long")
    assert et.step(None, "long") == ("buy", "long")
    assert et.step("long", None) == ("sell", "long")
    assert et.step("long", "inverse") == ("sell", "long")  # 판 돈이 들어온 다음 실행에 인버스를 산다
    assert et.step(None, "inverse") == ("buy", "inverse")


def test_리플레이는_폭락_날을_미리_보지_않는다() -> None:
    """판정일(300, 상승) 다음 날 100 으로 폭락 — 미리 보면 하락 국면으로 인버스를 들어 오히려 번다"""
    r = et.replay(_줄([100.0 + i for i in range(201)] + [100.0]), cost=0.0)
    assert r["twr_pct"] == pytest.approx(0.98 * (100 / 300 - 1) * 100)
