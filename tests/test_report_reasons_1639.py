"""리포트 2부 제외 사유가 사실과 맞게 (docs/infra.md 25.1639, docs/design.md 3.9 표)."""

from __future__ import annotations

import random

import pytest

from batch.services import report_picks as rp


def _행(sid: int, t: str, **kw: object) -> rp.SignalRow:
    base = dict(stock_id=sid, ticker=t, name=t, market="KOSPI", horizon="mid", signal_type="x", currency="KRW",
                buy_zone_low=9000, buy_zone_high=10000, suggested_weight_pct=4.0, suggested_amount=4_000_000,
                size_reduction=0.4, total_score=90,
                tranche_plan=[{"price": 10000, "ratio": 0.4, "amount": 1_600_000},
                              {"price": 9500, "ratio": 0.3, "amount": 1_200_000},
                              {"price": 9000, "ratio": 0.3, "amount": 1_200_000}],
                criteria=[{"label": "a", "display": "b", "threshold": "c", "source": "d", "as_of": "2026-10-20"}],
                as_of_date="2026-10-20", sector="S")  # fmt: skip
    base.update(kw)
    return rp.SignalRow(**base)  # type: ignore[arg-type]


def test_상관으로_줄어_빠지면_그_과정과_닮은_보유를_적는다() -> None:
    """예전 "이미 권장 비중(1.2%) 이상 보유" — 1부 근거표의 권장 비중은 4.0% 였고 빠진 까닭(보유와 상관)이 없었다."""
    rnd = random.Random(1)
    날 = [f"2026-{m:02d}-{d:02d}" for m in range(3, 11) for d in range(1, 29)][:140]
    p, a, b = 100.0, {}, {}
    for d in 날:
        p *= 1 + rnd.gauss(0, 0.02)
        a[d], b[d] = p, p * (1 + rnd.gauss(0, 0.001))
    보유 = [rp.Holding(1, "AAA", "AAA", "S1", 2_000_000), rp.Holding(2, "BBB", "BBB", "S2", 5_000_000)]
    v = rp.build_portfolio([_행(1, "AAA")], 100_000_000, holdings=보유, price_series={1: a, 2: b},
                           min_order_amount=100_000)  # fmt: skip
    assert v.allocations == []
    [e] = v.excluded
    assert e.reason == "종목 비중 상한 도달"
    assert e.detail == "보유 2.0% — 이미 권장 비중 4.0% × 상관 배수 0.30(보유 BBB와 상관 1.00) = 1.2% 이상 보유"


def test_상관이_없으면_예전_글() -> None:
    v = rp.build_portfolio([_행(1, "AAA")], 100_000_000, holdings=[rp.Holding(1, "AAA", "AAA", "S1", 5_000_000)])
    assert [e.detail for e in v.excluded] == ["보유 5.0% — 이미 권장 비중(4.0%) 이상 보유"]


def test_총액이_없으면_환율이_없어도_총액_미설정(monkeypatch: pytest.MonkeyPatch) -> None:
    """예전엔 환율이 없으면 총액 0 이어도 "환율 없음(설정은 그대로 둔다)" — 총액을 넣어야 할 사용자를 엉뚱한 곳으로."""
    from batch.core import db
    from batch.jobs import signals as sj
    from batch.services import fx

    monkeypatch.setattr(fx, "latest_rate", lambda *_a, **_k: None)
    monkeypatch.setattr(db, "get_setting", lambda _c, _k, d=None, **_kw: d)
    monkeypatch.setattr(db, "get_setting_in_range", lambda _c, _k, _d: (None, None))
    s = sj.load_settings(object(), "USD", "2026-10-20")  # type: ignore[arg-type]
    assert s["total_investable"] == 0.0 and s["fx_missing"] is False
    v = rp.build_portfolio([_행(1, "AAA", currency="USD", suggested_amount=None)], s["total_investable"],
                           currency="USD", fx_missing=s["fx_missing"])  # fmt: skip
    assert [e.reason for e in v.excluded] == ["총 투자가능금액 미설정"]
    # 총액이 있으면 그대로 "환율 없음"
    monkeypatch.setattr(db, "get_setting_in_range",
                        lambda _c, k, d: (10_000_000.0, None) if k == "total_investable_amount" else (None, None))  # fmt: skip
    assert sj.load_settings(object(), "USD", "2026-10-20")["fx_missing"] is True  # type: ignore[arg-type]
