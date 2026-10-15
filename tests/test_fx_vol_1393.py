"""미국 2부 후보의 원화 기준 변동성 배수 — 기록만 (docs/infra.md 25.1393, docs/factors.md 12.28 P2)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from batch.services import report_picks as rp
from tests.test_report_picks import row


def _날들(n: int) -> list[str]:
    시작 = date(2026, 3, 2)
    return [(시작 + timedelta(days=i)).isoformat() for i in range(n)]


def _미국(sid: int) -> rp.SignalRow:
    r = row(sid)
    r.currency, r.market = "USD", "NASDAQ"
    return r


def _계열(수익: list[float], 시작: float) -> dict[str, float]:
    out, v = {}, 시작
    for d, r in zip(_날들(len(수익) + 1), [0.0, *수익], strict=True):
        v *= 1 + r
        out[d] = v
    return out


def test_달러와_주식이_반대로_움직이면_원화_기준_변동성이_작다() -> None:
    주식 = [0.02 if i % 2 else -0.02 for i in range(80)]
    환 = [-x / 2 for x in 주식]  # 주식이 오르면 원화 강세 — 서로 상쇄
    out = rp.fx_vol_log([_미국(1)], {1: _계열(주식, 100.0)}, _계열(환, 1_400.0))
    assert out["lag0"]["n"] == 1 and out["lag0"]["kappa_median"] < 1 and out["lag0"]["kappa_over1"] == 0
    assert out["lag0"]["rho_median"] == pytest.approx(-1.0, abs=1e-6)


def test_같은_방향이면_크다_하루_민_쌍도_따로_남긴다() -> None:
    주식 = [0.02 if i % 2 else -0.02 for i in range(80)]
    out = rp.fx_vol_log([_미국(1)], {1: _계열(주식, 100.0)}, _계열([x / 2 for x in 주식], 1_400.0))
    assert out["lag0"]["kappa_over1"] == 1
    assert out["lag1"]["n"] == 1 and out["lag1"]["rho_median"] < 0  # 하루 밀면 교대 계열이라 부호가 뒤집힌다


def test_국내_종목과_짧은_계열은_세지_않는다() -> None:
    짧은 = rp.fx_vol_log([_미국(1)], {1: _계열([0.01] * 30, 100.0)}, _계열([0.0] * 30, 1_400.0))
    assert 짧은["lag0"] == {"n": 0, "unknown": 1}
    국내 = rp.fx_vol_log([row(2)], {2: _계열([0.01] * 80, 100.0)}, _계열([0.0] * 80, 1_400.0))
    assert 국내["lag0"] == {"n": 0, "unknown": 0}
