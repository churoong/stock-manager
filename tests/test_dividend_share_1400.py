"""배당 지수 몫의 기록 보유 — 몫이 빠진 날과 같은 창 규칙 (docs/infra.md 25.1400, 포트폴리오 감사)."""

from __future__ import annotations

import pytest

from batch.services import portfolio as pf

T = pf.Trade
R = {"KRW": pf.CostRates(0, 0, 0)}


def _twr(trades: list[pf.Trade], d: pf.DividendReceipt, closes: dict[str, float], end: str) -> float:
    rows, _ = pf.value_series(trades, [d], {1: closes}, {"USD": {}}, end, R)
    return rows[-1].twr_index


def test_창_앞_매매는_창이_열릴_때_보유로() -> None:
    # 100주 사서 한 달 뒤 90주 팔았다 — 창(지급일 앞 183일)이 열릴 때 보유는 10주. 500주분 배당(받은 주식 수 500)
    tr = [T(1, 1, "buy", "2025-01-10", 50_000, 100, "KRW", 1, 0, None),
          T(2, 1, "sell", "2025-02-10", 50_000, 90, "KRW", 1, 0, 0)]  # fmt: skip
    d = pf.DividendReceipt(1, "2026-04-20", 500_000.0, 1.0, 500.0)
    assert pf.배당_지수_몫(d, tr) == pytest.approx(10 / 500)  # 예전 100/500 = 0.2
    closes = {"2025-01-10": 50_000, "2026-04-17": 50_000, "2026-04-20": 50_000, "2026-04-21": 50_000}
    assert _twr(tr, d, closes, "2026-04-21") == pytest.approx(1.02)  # 예전 1.20


def test_하루_안_왕복의_장중_정점은_세지_않는다() -> None:
    tr = [T(1, 1, "buy", "2025-12-01", 50_000, 10, "KRW", 1, 0, None),
          T(2, 1, "buy", "2026-03-02", 50_000, 1000, "KRW", 1, 0, None),
          T(3, 1, "sell", "2026-03-02", 50_000, 1000, "KRW", 1, 0, 0)]  # fmt: skip
    d = pf.DividendReceipt(1, "2026-04-20", 500_000.0, 1.0, 500.0)
    assert pf.배당_지수_몫(d, tr) == pytest.approx(10 / 500)  # 예전 1000/500 → 1.0
    closes = {"2025-12-01": 50_000, "2026-03-02": 50_000, "2026-04-17": 50_000, "2026-04-20": 50_000}
    assert _twr(tr, d, closes, "2026-04-20") == pytest.approx(1.02)  # 예전 2.0
    # 주식 수를 비우면 원가 10% 상한이 장중 정점으로 커지지 않아 빠진다(경고) — 예전엔 통과했다
    assert pf.배당_지수_몫(pf.DividendReceipt(1, "2026-04-20", 500_000.0, 1.0, None), tr) is None
