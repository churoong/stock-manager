"""추정 거래세의 법정 세율은 결제일(T+2) 기준 — 연말 마지막 두 거래일 체결은 새해 세율 (docs/infra.md 25.1298, 손익 감사 #3)."""

from __future__ import annotations

from batch.jobs import portfolio as job
from batch.services import backtest as bt
from batch.services import portfolio as pf

표 = dict(job.체결_기준_세율표(bt.KR_SELL_TAX_SCHEDULE))


def test_시행일이_결제_기준으로_옮겨진다() -> None:
    # 2026-01-01 시행 → 첫 거래일 01-02 → 두 거래일 앞(12-31 휴장) 2025-12-29
    assert 표["2025-12-29"] == 0.2 and 표["2024-12-27"] == 0.15 and 표["0001-01-01"] == 0.3


def _세금(day: str) -> float:
    rates = job.cost_rates({"kr_buy_pct": 0, "kr_sell_pct": 0}, {})[0]["KRW"]
    t = pf.Trade(1, 1, "sell", day, 10_000.0, 1000, "KRW", 1.0, fee=0)
    return pf.fee_and_tax(t, rates)[1]


def test_연말_체결은_결제일의_세율() -> None:
    assert round(_세금("2024-12-26")) == 18_000  # 12-30 결제 — 2024 년 0.18%
    assert round(_세금("2024-12-27")) == 15_000  # 2025-01-02 결제 — 2025 년 0.15%
    assert round(_세금("2025-12-26")) == 15_000  # 12-30 결제 — 2025 년 0.15%
    # 2025-12-29 체결(2026-01-02 결제)부터는 지금 구간 — 설정 세율(여기선 비움 → 0)을 쓴다
    assert _세금("2025-12-29") == 0.0
