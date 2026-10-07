"""분할·목표·손절 가격이 호가 단위다 (docs/infra.md 25.657, 감사)."""

from __future__ import annotations

import pytest

from batch.services import signals as sg


@pytest.mark.parametrize(
    ("가격", "단위"),
    [(1_999, 1), (2_000, 5), (4_995, 5), (19_990, 10), (20_000, 50), (73_260, 100), (200_000, 500), (500_000, 1_000)],
)
def test_국내_호가_단위(가격: float, 단위: float) -> None:
    assert sg.tick_size(가격, "KRW") == 단위


def test_맞추기() -> None:
    assert sg.to_tick(73_260, "KRW") == 73_300
    assert sg.to_tick(72_520, "KRW", "up") == 72_600
    assert sg.to_tick(74_080, "KRW", "down") == 74_000
    assert sg.to_tick(123.456, "USD") == 123.46
    assert sg.to_tick(0.123456, "USD") == 0.1235
    assert sg.to_tick(123.456, "JPY") == 123.456  # 모르는 통화는 그대로


def test_분할_가운데도_호가이고_구간_안이다() -> None:
    tr = sg.tranche_plan(72_600, 74_000, "KRW")
    assert [t.price for t in tr] == [74_000, 73_300, 72_600]
    assert sum(t.ratio for t in tr) == pytest.approx(1.0)


@pytest.mark.parametrize("입력", ["short_input", "mid_input", "long_input"])
def test_실제_신호의_가격이_모두_호가다(입력: str) -> None:
    from tests import test_signals as ts

    신호들 = sg.evaluate(getattr(ts, 입력)(), total_investable=10_000_000, min_order_amount=0)
    assert 신호들, "신호가 나야 본다"
    for s in 신호들:
        가격들 = [s.buy_zone_low, s.buy_zone_high, s.target_price, s.stop_price, *(t.price for t in s.tranches)]
        for p in 가격들:
            단위 = sg.tick_size(p, s.currency)
            assert 단위 and abs(p / 단위 - round(p / 단위)) < 1e-6, (s.horizon, p)
        assert s.buy_zone_low <= s.tranches[1].price <= s.buy_zone_high


def test_반값은_올린다() -> None:
    """파이썬 round 는 짝수 쪽이라 73,250 이 73,200 이 됐다 (docs/infra.md 25.661, 교차검증)."""
    assert sg.to_tick(73_250, "KRW") == 73_300
    assert sg.to_tick(73_150, "KRW") == 73_200


@pytest.mark.parametrize("입력", ["short_input", "mid_input", "long_input"])
def test_근거표의_분할_가운데가_카드와_같다(입력: str) -> None:
    """근거표만 반올림 전 중앙을 적어 카드와 달랐다 (25.661, 교차검증)."""
    import re

    from tests import test_signals as ts

    for s in sg.evaluate(getattr(ts, 입력)(), total_investable=10_000_000, min_order_amount=0):
        행들 = [r for r in s.rationale_data.get("criteria", []) if r["label"] == "매수 구간·분할 (참고)"]
        if not 행들:
            continue
        m = re.search(r"분할 [^/]+/ ([\d,.]+)", 행들[0]["display"])
        assert m and float(m.group(1).replace(",", "")) == pytest.approx(s.tranches[1].price), 행들[0]["display"]


def test_근거표_분할_가운데는_호가다() -> None:
    """구간 72,600~73,900 → 카드 73,300(반값 올림), 근거표는 73,250 이었다 (25.661, 교차검증)."""
    from tests import test_signals as ts

    행들 = sg.plan_criteria(ts.short_input(), "short", 72_600, 73_900, 80_000, 68_000, None, None, 10_000_000, 10.0)
    분할 = next(r for r in 행들 if r["label"] == "매수 구간·분할 (참고)")
    assert "/ 73,300원 /" in 분할["display"], 분할["display"]


def test_묵은_사업보고서는_판정표가_까닭을_말한다() -> None:
    """"값 없음" 만 보여 왜인지 몰랐다 (docs/infra.md 25.663, 교차검증)."""
    from tests import test_signals as ts

    inp = ts.mid_input(revenue_growth=None, operating_income_growth=None, annual_stale="최신 사업보고서가 FY2023")
    행들 = sg.judgements(inp)["mid"][1]
    신선 = [r for r in 행들 if r["label"] == "사업보고서 신선도"]
    assert 신선 and 신선[0]["passed"] is False and "FY2023" in 신선[0]["display"]
