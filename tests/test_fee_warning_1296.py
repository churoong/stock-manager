"""수수료율 없음 경고도 그 율을 실제로 쓰는 매매가 있을 때만 (docs/infra.md 25.1296, 손익 감사 #4)."""

from __future__ import annotations

from batch.jobs import portfolio as job
from batch.services import portfolio as pf

율없음 = {"KRW": pf.CostRates(sell_tax_pct=0.15), "USD": pf.CostRates(has_sell_tax=False)}
국내만율 = {"KRW": pf.CostRates(0.015, 0.015, 0.15), "USD": pf.CostRates(has_sell_tax=False)}


def _t(side: str, cur: str, fee: float | None) -> pf.Trade:
    return pf.Trade(1, 1, side, "2026-10-01", 100.0, 1, cur, 1.0 if cur == "KRW" else 1400.0, fee=fee, tax=0)


def _경고(rates: dict, trades: list | None) -> bool:
    return job._안_넣은_수수료 in job._안_넣은_비용(rates, trades)


def test_수수료를_다_넣었으면_경고하지_않는다() -> None:
    assert not _경고(율없음, [_t("buy", "KRW", 10.0), _t("sell", "KRW", 10.0)])


def test_국내만_매매하면_미국_율이_비어도_경고하지_않는다() -> None:
    assert not _경고(국내만율, [_t("buy", "KRW", None), _t("sell", "KRW", None)])


def test_율도_수수료도_비면_경고() -> None:
    assert _경고(율없음, [_t("buy", "KRW", None)])
    assert _경고(국내만율, [_t("buy", "USD", None)])


def test_매매를_안_주면_예전처럼() -> None:
    assert _경고(국내만율, None)
