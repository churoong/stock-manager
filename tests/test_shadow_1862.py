"""단타·스윙 판정 순수 함수 — 손으로 계산한 값 (docs/paper.md 16장, docs/infra.md 25.1862)."""

from __future__ import annotations

import pytest

from batch.services import shadow as sh

비용 = sh.Costs(buy_fee_pct=0.015, sell_fee_pct=0.015, sell_tax_pct=0.15, slippage_pct=0.10)
무비용 = sh.Costs(0.0, 0.0, 0.0, 0.0)


def B(d: str, o: float, h: float, lo: float, c: float) -> sh.Bar:
    return sh.Bar(d, o, h, lo, c)


def test_당일_단타는_시가에_사서_종가에_판다() -> None:
    t = sh.simulate(1, "2026-10-01", [B("2026-10-02", 100, 106, 98, 104)], sh.DAY, 무비용, "KR")
    assert (t.status, t.entry_date, t.exit_date, t.reason, t.sessions) == ("closed", "2026-10-02", "2026-10-02", "close", 1)
    assert t.net == pytest.approx(0.04)
    # 비용: 진입 100.1, 청산 104 × 0.999 = 103.896, 산 100.1 × 1.00015, 판 103.896 × (1 − 0.00165)
    t2 = sh.simulate(1, "2026-10-01", [B("2026-10-02", 100, 106, 98, 104)], sh.DAY, 비용, "KR")
    assert t2.net == pytest.approx(103.896 * (1 - 0.00165) / (100.1 * 1.00015) - 1, abs=1e-6)


def test_스윙_목표_손절_기한() -> None:
    목표 = sh.simulate(1, "d0", [B("d1", 100, 102, 99, 101), B("d2", 101, 105.5, 100, 104)], sh.SWING, 무비용, "KR")
    assert (목표.reason, 목표.exit_price, 목표.sessions) == ("target", 105.0, 2)
    손절 = sh.simulate(1, "d0", [B("d1", 100, 101, 96.5, 98)], sh.SWING, 무비용, "KR")
    assert (손절.reason, 손절.exit_price, 손절.net) == ("stop", 97.0, pytest.approx(-0.03))
    기한 = sh.simulate(1, "d0", [B(f"d{i}", 100, 101, 99, 100.5) for i in range(1, 12)], sh.SWING, 무비용, "KR")
    assert (기한.reason, 기한.sessions, 기한.exit_date, 기한.net) == ("time", 10, "d10", pytest.approx(0.005))


def test_스윙_같은_날_둘_다_닿으면_손절_시가_건너뛰기() -> None:
    둘다 = sh.simulate(1, "d0", [B("d1", 100, 106, 96, 101)], sh.SWING, 무비용, "KR")
    assert 둘다.reason == "stop"
    아래 = sh.simulate(1, "d0", [B("d1", 100, 101, 99, 100), B("d2", 95, 96, 94, 95)], sh.SWING, 무비용, "KR")
    assert (아래.reason, 아래.exit_price) == ("stop_gap", 95.0)  # 손절선(97)보다 나쁜 시가
    위 = sh.simulate(1, "d0", [B("d1", 100, 101, 99, 100), B("d2", 107, 108, 106, 107)], sh.SWING, 무비용, "KR")
    assert (위.reason, 위.exit_price) == ("target_gap", 107.0)


def test_진행_중_체결_못_함() -> None:
    assert sh.simulate(1, "d0", [], sh.DAY, 무비용, "KR").status == "open"  # 다음 장이 아직 없다
    진행 = sh.simulate(1, "d0", [B("d1", 100, 101, 99, 100)], sh.SWING, 무비용, "KR")
    assert 진행.status == "open" and 진행.net is None
    상한 = sh.simulate(1, "d0", [B("d1", 130, 130, 130, 130)], sh.DAY, 무비용, "KR", prev_close=100)
    assert (상한.status, 상한.reason) == ("no_entry", "상한가 시가")
    assert sh.simulate(1, "d0", [B("d1", 130, 130, 130, 130)], sh.DAY, 무비용, "US", prev_close=100).status == "closed"


def test_집계() -> None:
    ts = [
        sh.Trade(1, "d", "day_v1", "closed", net=0.02, sessions=1, reason="close"),
        sh.Trade(2, "d", "day_v1", "closed", net=-0.01, sessions=1, reason="close"),
        sh.Trade(3, "d", "day_v1", "closed", net=0.04, sessions=1, reason="close"),
        sh.Trade(4, "d", "day_v1", "open"),
        sh.Trade(5, "d", "day_v1", "no_entry", reason="상한가 시가"),
        sh.Trade(6, "d", "swing_v1", "closed", net=0.5, sessions=3, reason="target"),
    ]
    s = sh.summarize(ts, "day_v1")
    assert (s.n, s.open, s.no_entry, s.win_rate) == (3, 1, 1, pytest.approx(2 / 3, abs=1e-4))
    assert s.avg_net == pytest.approx(0.05 / 3, abs=1e-6) and s.median_net == pytest.approx(0.02)
    assert s.profit_factor == pytest.approx(6.0) and s.reasons == {"close": 3}
    assert sh.summarize([], "day_v1").n == 0


def test_규칙은_한_벌만_사전_등록() -> None:
    assert [r.name for r in sh.RULES] == ["day_v1", "swing_v1"]
    assert (sh.SWING.target_pct, sh.SWING.stop_pct, sh.SWING.max_sessions) == (0.05, -0.03, 10)
