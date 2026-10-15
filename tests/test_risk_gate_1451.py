"""주문 전 위험 점검 (docs/paper.md 11장, docs/infra.md 25.1451) — 배분과 따로 한 번 더 막는 관문."""

from __future__ import annotations

from batch.services import risk_gate as rg


def _문맥(**kw) -> rg.Context:  # noqa: ANN003
    기본 = dict(equity_krw=1_000_000, day_return_pct=0.0, cash_available_krw=500_000,
              holding_krw={1: 80_000}, sector_of={1: "전자", 2: "전자", 3: "화학"}, max_stock_pct=10.0,
              max_sector_pct=30.0, today="2026-10-16")  # fmt: skip
    return rg.Context(**{**기본, **kw})


def _코드(막힘: list) -> list[set[str]]:
    return [{r[0] for r in 까닭} for _c, 까닭 in 막힘]


def test_buy_passes_when_everything_is_fine() -> None:
    통과, 막힘 = rg.gate([rg.Candidate(3, "buy", 50_000, "2026-10-15")], _문맥())
    assert len(통과) == 1 and 막힘 == []


def test_stock_cap_recheck_counts_holding_and_earlier_orders() -> None:
    # 보유 8만 + 1회차 1.5만 = 9.5% 통과, 2회차 1.5만 더하면 11% > 10% + 0.5 → 막힘
    통과, 막힘 = rg.gate([rg.Candidate(1, "buy", 15_000, "2026-10-15", 0), rg.Candidate(1, "buy", 15_000, "2026-10-15", 1)],
                       _문맥())  # fmt: skip
    assert [c.key for c in 통과] == [0] and _코드(막힘) == [{"stock_cap"}]


def test_sector_cap_cash_stale_and_daily_loss() -> None:
    _, 막힘 = rg.gate([rg.Candidate(2, "buy", 250_000, "2026-10-15")], _문맥(max_stock_pct=50.0))
    assert _코드(막힘) == [{"sector_cap"}]  # 전자 8만 + 25만 = 33% > 30%
    _, 막힘 = rg.gate([rg.Candidate(3, "buy", 90_000, "2026-10-15")], _문맥(cash_available_krw=50_000))
    assert _코드(막힘) == [{"cash"}]
    _, 막힘 = rg.gate([rg.Candidate(3, "buy", 50_000, "2026-10-01"), rg.Candidate(3, "buy", 1, None)], _문맥())
    assert _코드(막힘) == [{"stale"}, {"no_ref"}]
    _, 막힘 = rg.gate([rg.Candidate(3, "buy", 50_000, "2026-10-15")], _문맥(day_return_pct=-3.8))
    assert _코드(막힘) == [{"daily_loss"}]
    통과, _ = rg.gate([rg.Candidate(3, "buy", 50_000, "2026-10-15")], _문맥(day_return_pct=None))
    assert len(통과) == 1  # 첫날은 하루 수익을 모른다 — 막지 않는다


def test_sells_only_stop_for_halt_or_corporate_action() -> None:
    """위험을 줄이는 주문을 손실 한도로 막으면 거꾸로다."""
    통과, 막힘 = rg.gate([rg.Candidate(1, "sell", 0.0)], _문맥(day_return_pct=-50.0, cash_available_krw=0))
    assert len(통과) == 1 and not 막힘
    _, 막힘 = rg.gate([rg.Candidate(1, "sell", 0.0), rg.Candidate(3, "buy", 1, "2026-10-15")], _문맥(halted=True))
    assert _코드(막힘) == [{"halt"}, {"halt"}]
    _, 막힘 = rg.gate([rg.Candidate(1, "sell", 0.0)], _문맥(frozen=frozenset({1})))
    assert _코드(막힘) == [{"frozen"}]


def test_order_count_limit_and_summary() -> None:
    후보 = [rg.Candidate(3, "buy", 1, "2026-10-15", i) for i in range(rg.MAX_ORDERS_PER_DAY + 2)]
    통과, 막힘 = rg.gate(후보, _문맥(max_stock_pct=100.0, max_sector_pct=100.0))
    assert len(통과) == rg.MAX_ORDERS_PER_DAY and len(막힘) == 2
    assert rg.summary(막힘) == {"blocked": 2, "by_reason": {"max_orders": 2}}


def test_cap_tolerance_is_relative_for_large_caps() -> None:
    """상한 15% 의 정상 배분이 예약 여유로 15.6% 가 되어도 막지 않는다 (25.1453, 교차검증 권장)."""
    통과, _ = rg.gate([rg.Candidate(3, "buy", 156_000, "2026-10-15")], _문맥(max_stock_pct=15.0, max_sector_pct=100.0))
    assert len(통과) == 1
    _, 막힘 = rg.gate([rg.Candidate(3, "buy", 158_000, "2026-10-15")], _문맥(max_stock_pct=15.0, max_sector_pct=100.0))
    assert _코드(막힘) == [{"stock_cap"}]
