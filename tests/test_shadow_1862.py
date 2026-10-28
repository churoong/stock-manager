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
    assert [r.name for r in sh.RULES] == ["day_v1", "swing_v1", "swing_close_v1"]
    assert (sh.SWING.target_pct, sh.SWING.stop_pct, sh.SWING.max_sessions) == (0.05, -0.03, 10)
    # 종가 판정 스윙은 판 1 과 같은 값 — 판정 꼴만 다르다(25.1867)
    assert (sh.SWING_CLOSE.target_pct, sh.SWING_CLOSE.stop_pct, sh.SWING_CLOSE.max_sessions) == (0.05, -0.03, 10)


def test_비용_있는_스윙_손절_기한과_선_기준은_슬리피지_전_시가() -> None:
    """25.1863 (25.1862 교차검증 [권고]) — 스윙 시험이 무비용뿐이라 손절·기한의 슬리피지와 선 기준이 묶이지 않았다"""
    s = 비용.slippage_pct / 100
    손절 = sh.simulate(1, "d0", [B("d1", 100, 101, 96.5, 98)], sh.SWING, 비용, "KR")
    assert 손절.exit_price == pytest.approx(97 * (1 - s)) and 손절.entry_price == pytest.approx(100 * (1 + s))
    기한 = sh.simulate(1, "d0", [B(f"d{i}", 100, 101, 99, 100.5) for i in range(1, 12)], sh.SWING, 비용, "KR")
    assert 기한.exit_price == pytest.approx(100.5 * (1 - s))
    # 선 기준은 시가 100 — 고가 104.95 는 목표(105) 아래, 고가 105.0 은 목표. 진입가(100.1) 기준이면 105.105 라 갈린다
    assert sh.simulate(1, "d0", [B("d1", 100, 105.0, 99, 104)], sh.SWING, 비용, "KR").reason == "target"


def test_시세가_끊기면_마지막_종가로_닫는다() -> None:
    """25.1863 (25.1862 교차검증 [권고]) — 진입 뒤 상장폐지·장기 정지면 영영 진행 중으로 남아 떨어진 뒤 끊기는 종목이 빠졌다"""
    줄 = [B("d1", 100, 101, 98.5, 99), B("d2", 99, 99, 97.5, 98)]
    assert sh.simulate(1, "d0", 줄, sh.SWING, 무비용, "KR").status == "open"
    끊 = sh.simulate(1, "d0", 줄, sh.SWING, 무비용, "KR", ended=True)
    assert (끊.status, 끊.reason, 끊.exit_price, 끊.exit_date) == ("closed", "끊김", 98.0, "d2")
    assert sh.simulate(1, "d0", [], sh.DAY, 무비용, "KR", ended=True).reason == "시세 끊김"


def test_매도세는_청산일_세율() -> None:
    일정 = sh.Costs(0.0, 0.0, 0.15, 0.0, (("2026-01-01", 0.15), ("2026-12-01", 0.20)))
    앞 = sh.simulate(1, "d0", [B("2026-11-02", 100, 101, 99, 100)], sh.DAY, 일정, "KR")
    뒤 = sh.simulate(1, "d0", [B("2026-12-02", 100, 101, 99, 100)], sh.DAY, 일정, "KR")
    assert 앞.net == pytest.approx(-0.0015) and 뒤.net == pytest.approx(-0.0020)


def test_집계는_종목_수도_센다() -> None:
    ts = [sh.Trade(1, "d1", "day_v1", "closed", net=0.01, sessions=1), sh.Trade(1, "d2", "day_v1", "closed", net=0.01,
          sessions=1), sh.Trade(2, "d1", "day_v1", "closed", net=-0.01, sessions=1)]  # fmt: skip
    assert sh.summarize(ts, "day_v1").stocks == 2


def test_종가_판정_스윙은_넘은_날_다음_장_시가에_판다() -> None:
    """25.1867 — 가상모의투자 엔진(일봉 시가·종가, 장 전 시장가 매도)이 돌릴 수 있는 꼴"""
    R = sh.SWING_CLOSE
    # 장중 고가가 목표(105)에 닿아도 종가가 아래면 팔지 않는다 — swing_v1 과 갈리는 자리
    닿기만 = sh.simulate(1, "d0", [B("d1", 100, 106, 99, 104), B("d2", 104, 104, 103, 103.5)], R, 무비용, "KR")
    assert 닿기만.status == "open" and sh.simulate(1, "d0", [B("d1", 100, 106, 99, 104)], sh.SWING, 무비용, "KR").reason == "target"
    목표 = sh.simulate(1, "d0", [B("d1", 100, 106, 99, 105.5), B("d2", 103, 104, 102, 103)], R, 무비용, "KR")
    assert (목표.reason, 목표.exit_date, 목표.exit_price, 목표.sessions) == ("target_next_open", "d2", 103.0, 2)
    손절 = sh.simulate(1, "d0", [B("d1", 100, 101, 96, 96.9), B("d2", 94, 95, 93, 94)], R, 무비용, "KR")
    assert (손절.reason, 손절.exit_price, 손절.net) == ("stop_next_open", 94.0, pytest.approx(-0.06))
    # 같은 날 종가가 손절선 위·목표 아래면 아무것도 아니다 — 저가가 손절에 닿아도
    assert sh.simulate(1, "d0", [B("d1", 100, 101, 95, 99), B("d2", 99, 99, 98, 98)], R, 무비용, "KR").status == "open"
    줄 = [B(f"d{i}", 100, 101, 99, 100.5) for i in range(1, 13)]
    기한 = sh.simulate(1, "d0", 줄, R, 무비용, "KR")
    assert (기한.reason, 기한.exit_date, 기한.sessions) == ("time_next_open", "d11", 11)  # 10번째 종가에 정해 11번째 시가
    assert sh.simulate(1, "d0", 줄[:10], R, 무비용, "KR").status == "open"  # 정했지만 다음 장이 아직 없다
    s = 비용.slippage_pct / 100
    비용목표 = sh.simulate(1, "d0", [B("d1", 100, 106, 99, 105.5), B("d2", 103, 104, 102, 103)], R, 비용, "KR")
    assert 비용목표.exit_price == pytest.approx(103 * (1 - s))  # 시장가 매도 — 슬리피지 불리하게


def test_종가_판정_스윙도_끊기면_마지막_종가로_닫는다() -> None:
    줄 = [B("d1", 100, 101, 98.5, 99), B("d2", 99, 99, 96, 96.5)]  # d2 종가가 손절선 아래인데 다음 장이 없다
    assert sh.simulate(1, "d0", 줄, sh.SWING_CLOSE, 무비용, "KR").status == "open"
    끊 = sh.simulate(1, "d0", 줄, sh.SWING_CLOSE, 무비용, "KR", ended=True)
    assert (끊.reason, 끊.exit_price, 끊.exit_date) == ("끊김", 96.5, "d2")
