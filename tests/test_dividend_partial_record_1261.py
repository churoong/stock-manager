"""기록하지 않은 주식의 배당이 시간가중 수익률을 부풀리지 않는다 (docs/infra.md 25.1261, 손익 감사 재현)."""

from __future__ import annotations

import pytest

from batch.services import portfolio as pf
from tests.test_portfolio import NO_COST, buy

CLOSES = {1: {"2026-03-02": 55_000.0, "2026-04-17": 55_000.0, "2026-04-20": 55_000.0}}


def _지수(trades: list[pf.Trade], receipt: pf.DividendReceipt) -> tuple[float, list[str]]:
    rows, warnings = pf.value_series(trades, [receipt], CLOSES, {}, "2026-04-20", {"KRW": NO_COST})
    return rows[-1].twr_index, warnings


def test_주식_수를_적으면_기록한_몫만_넣는다() -> None:
    # 500주 가운데 100주만 기록 — 배당 세후 154,230원은 500주 몫
    지수, _ = _지수([buy(1, "2026-03-02", 55_000, 100, fee=0)], pf.DividendReceipt(1, "2026-04-20", 154_230.0, 1.0, 500))
    assert 지수 == pytest.approx(1 + 154_230 / 5 / 5_500_000)  # 예전엔 +2.8%


def test_주식_수를_모르고_원가에_견줘_너무_크면_빼고_경고한다() -> None:
    지수, 경고 = _지수([buy(1, "2026-03-02", 55_000, 1, fee=0)], pf.DividendReceipt(1, "2026-04-20", 154_230.0, 1.0))
    assert 지수 == pytest.approx(1.0)  # 예전엔 3.80
    assert any("너무 큰 배당" in w for w in 경고)


def test_보통_배당은_그대로() -> None:
    지수, 경고 = _지수([buy(1, "2026-03-02", 55_000, 100, fee=0)], pf.DividendReceipt(1, "2026-04-20", 154_230.0, 1.0))
    assert 지수 == pytest.approx(1 + 154_230 / 5_500_000)
    assert not any("너무 큰 배당" in w for w in 경고)
    # 주식 수가 기록과 같거나 적으면 다 넣는다
    지수2, _ = _지수([buy(1, "2026-03-02", 55_000, 100, fee=0)], pf.DividendReceipt(1, "2026-04-20", 154_230.0, 1.0, 100))
    assert 지수2 == pytest.approx(지수)
