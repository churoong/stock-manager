"""요약 머리의 환율과 다른 체결 환율로 평가한 외화 보유를 센다 (docs/infra.md 25.1354, 거래 입력 감사 ③)."""

from __future__ import annotations

from pathlib import Path

from batch.services import portfolio as pf


def _pos(sid: int, ccy: str, fx_date: str | None) -> pf.Position:
    p = pf.Position(sid, 1, ccy, 100.0, 1400.0, 100.0, 140000.0, "2026-10-01", None)
    p.fx_date = fx_date
    return p


def test_표_날짜와_다른_외화_보유만_센다() -> None:
    보유 = [_pos(1, "USD", "2026-10-08"), _pos(2, "USD", "2026-10-12"), _pos(3, "KRW", "-"), _pos(4, "USD", None)]
    assert pf.trade_fx_valued(보유, "2026-10-08") == 1  # 10-12 체결 환율 하나 (원화·평가 못 함은 빼고)
    assert pf.trade_fx_valued(보유[:1], "2026-10-08") == 0


def test_요약과_화면이_그_수를_쓴다() -> None:
    root = Path(__file__).resolve().parents[1]
    assert '"fx_trade_valued": pf.trade_fx_valued(positions, latest_fx[0])' in (root / "batch/jobs/portfolio.py").read_text()
    assert "종목은 체결 환율로 평가" in (root / "web/components/PortfolioView.tsx").read_text()
