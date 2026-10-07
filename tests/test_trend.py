"""시장 추세 필터 (docs/signals.md 3.5). 국면 판정과 배수를 손으로 맞춘 값으로 고정한다."""

from __future__ import annotations

import datetime as dt

import pytest

from batch.jobs import index_prices as index_job
from batch.services import trend
from batch.sources.yfinance_src import DailyBar


def _series(days: int, last: float, level: float = 100.0, end: str = "2026-09-15") -> list[tuple[str, float]]:
    """end 에서 거꾸로 days 일. 마지막 날만 last, 나머지는 level."""
    end_day = dt.date.fromisoformat(end)
    out = []
    for i in range(days):
        d = (end_day - dt.timedelta(days=days - 1 - i)).isoformat()
        out.append((d, last if i == days - 1 else level))
    return out


class Test국면판정:
    def test_200일선_아래면_약세(self) -> None:
        # 199일 100 + 마지막 90 → SMA 99.95 > 90
        r = trend.regime_from_closes("KOSPI", _series(200, 90.0), "2026-09-15")
        assert r.state == "bear"
        assert r.sma == pytest.approx(99.95)
        assert r.close == 90.0 and r.days == 200

    def test_200일선_위면_강세(self) -> None:
        r = trend.regime_from_closes("KOSPI", _series(200, 110.0), "2026-09-15")
        assert r.state == "bull"
        assert "강세" in r.describe()

    def test_200일치_미만이면_미판정(self) -> None:
        r = trend.regime_from_closes("KOSPI", _series(150, 90.0), "2026-09-15")
        assert r.state == "unknown"
        assert "150일치" in (r.note or "")

    def test_오래된_지수는_미판정(self) -> None:
        # 마지막 지수가 9/15 인데 기준일이 9/30 → 15일 오래됨 (7일 한도)
        r = trend.regime_from_closes("KOSPI", _series(200, 90.0), "2026-09-30")
        assert r.state == "unknown"
        assert "오래됨" in (r.note or "")

    def test_기준일_뒤의_지수는_보지_않는다(self) -> None:
        # look-ahead 방지: 9/16 이후 값을 붙여도 9/15 기준 판정은 같다
        closes = _series(200, 90.0) + [("2026-09-16", 200.0), ("2026-09-17", 200.0)]
        r = trend.regime_from_closes("KOSPI", closes, "2026-09-15")
        assert r.state == "bear" and r.date == "2026-09-15"

    def test_아무것도_없으면_미판정(self) -> None:
        assert trend.regime_from_closes("SP500", [], "2026-09-15").state == "unknown"


class Test배수:
    BEAR = trend.regime_from_closes("KOSPI", _series(200, 90.0), "2026-09-15")
    BULL = trend.regime_from_closes("KOSPI", _series(200, 110.0), "2026-09-15")
    ON = {"enabled": True, "bear_factor": 0.5}

    def test_약세면_설정_배수(self) -> None:
        factor, data = trend.factor_for(self.BEAR, self.ON)
        assert factor == 0.5
        assert data["regime"]["state"] == "bear" and data["bear_factor"] == 0.5

    def test_강세면_1(self) -> None:
        assert trend.factor_for(self.BULL, self.ON)[0] == 1.0

    def test_모르면_줄이지_않고_그_사실을_남긴다(self) -> None:
        unknown = trend.regime_from_closes("KOSPI", [], "2026-09-15")
        factor, data = trend.factor_for(unknown, self.ON)
        assert factor == 1.0 and "regime_note" in data
        assert trend.factor_for(None, self.ON) == (1.0, {"regime": None, "regime_factor": 1.0, "regime_note": "국면을 몰라 줄이지 않았습니다"})

    def test_꺼져_있으면_아무것도_없다(self) -> None:
        assert trend.factor_for(self.BEAR, {"enabled": False, "bear_factor": 0.5}) == (1.0, {})


class Test설정과_지수매핑:
    def test_시장별_지수(self) -> None:
        assert trend.index_for_market("KOSPI") == "KOSPI"
        assert trend.index_for_market("kosdaq") == "KOSDAQ"
        assert trend.index_for_market("NASDAQ") == "SP500"
        assert trend.index_for_market("NYSE") == "SP500"
        assert trend.index_for_market("XX") is None
        assert trend.index_for_market(None) is None

    def test_설정_기본값과_범위(self) -> None:
        class C:
            def __init__(self, raw):
                self.raw = raw

            def execute(self, sql, args=None):
                import json

                from batch.core.turso import ResultSet

                rows = [] if self.raw is None else [(json.dumps(self.raw),)]
                return ResultSet(columns=["value"], rows=rows, last_insert_rowid=None)

        assert trend.load_settings(C(None)) == {"enabled": True, "bear_factor": 0.5, "warnings": []}  # type: ignore[arg-type]
        # 범위 밖은 **기본값으로 되돌리고 말한다** — 예전에는 1.0 으로 조용히 잘랐다 (docs/infra.md 25.260)
        밖 = trend.load_settings(C({"enabled": False, "bear_factor": 1.7}))  # type: ignore[arg-type]
        assert 밖["enabled"] is False and 밖["bear_factor"] == 0.5 and 밖["warnings"]
        글 = trend.load_settings(C({"bear_factor": "x"}))  # type: ignore[arg-type]
        assert 글["bear_factor"] == 0.5 and 글["warnings"]

    def test_리포트_한_줄(self) -> None:
        regimes = {"KOSPI": Test배수.BEAR, "KOSDAQ": Test배수.BULL}
        line = trend.report_line(regimes, Test배수.ON)
        assert line.startswith("시장 국면: KOSPI 90 < 200일선 100 → 약세")
        assert "×0.50" in line
        assert "꺼짐" in trend.report_line(regimes, {"enabled": False})


class Test수집행:
    def test_심볼을_지수_코드로_바꾸고_모르는_것은_버린다(self) -> None:
        bars = [
            DailyBar("^KS11", "2026-09-15", 2500.0),
            DailyBar("^GSPC", "2026-09-15", 6000.0),
            DailyBar("AAPL", "2026-09-15", 200.0),  # 지수가 아니다
            DailyBar("^KQ11", "2026-09-15", 0.0),  # 0 은 버린다
        ]
        rows = index_job.rows_from_bars(bars, "yfinance", "t")
        assert rows == [
            ("KOSPI", "2026-09-15", 2500.0, "yfinance ^KS11", "t"),
            ("SP500", "2026-09-15", 6000.0, "yfinance ^GSPC", "t"),
        ]


def test_확정_거래일_뒤_봉은_장중이라_버린다() -> None:
    """09:20 KST 복귀 워크플로가 코스피 장중 값을 오늘 종가로 넣었다 (docs/infra.md 25.608, 감사)."""
    bars = [
        DailyBar("^KS11", "2026-09-28", 2500.0),
        DailyBar("^KS11", "2026-09-29", 2510.0),  # 오늘 장중
        DailyBar("^GSPC", "2026-09-28", 6000.0),
    ]
    rows = index_job.rows_from_bars(bars, "yfinance", "t", {"KR": "2026-09-28", "US": "2026-09-28"})
    assert [(r[0], r[1]) for r in rows] == [("KOSPI", "2026-09-28"), ("SP500", "2026-09-28")]


def test_지수_하나가_빠지면_말하고_실행_기록을_남긴다(monkeypatch) -> None:
    """세 심볼 중 하나가 통째로 빠져도 행이 있어 조용했고, 수동 실행은 기록이 없었다 (docs/infra.md 25.610)."""
    import sys

    from batch.core import db
    from batch.sources import yfinance_src
    from batch.sources.yfinance_src import FetchResult
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    monkeypatch.setattr(index_job, "TursoClient", lambda: mem)
    monkeypatch.setattr(index_job, "settled_days", lambda: {"KR": "2026-09-28", "US": "2026-09-28"})
    monkeypatch.setattr(yfinance_src, "fetch_daily_bars", lambda *a, **k: FetchResult(ok=True, data=[
        DailyBar("^KS11", "2026-09-28", 2500.0), DailyBar("^GSPC", "2026-09-28", 6000.0)]))
    monkeypatch.setattr(sys, "argv", ["index_prices"])
    assert index_job.main() == 0
    status, error = mem.conn.execute(
        "SELECT status, error_text FROM batch_runs WHERE job_name = 'index_prices'"
    ).fetchone()
    assert status == "partial" and "KOSDAQ" in error
    db._열린_실행.clear()


def test_마감한_날은_그날이_확정이다() -> None:
    """국내 아침(뉴욕 전날 저녁)에 이미 마감한 미국 종가를 버려 S&P 500 이 하루 늦었다 (docs/infra.md 25.611)."""
    from datetime import UTC, datetime

    국내아침 = index_job.settled_days(datetime(2026, 9, 28, 23, 27, tzinfo=UTC))  # 9/29 08:27 KST = 9/28 19:27 ET
    assert 국내아침 == {"KR": "2026-09-28", "US": "2026-09-28"}
    국내장중 = index_job.settled_days(datetime(2026, 9, 29, 1, 0, tzinfo=UTC))  # 9/29 10:00 KST
    assert 국내장중["KR"] == "2026-09-28", "장중 봉은 아직 종가가 아니다"
    assert index_job.settled_days(datetime(2026, 9, 29, 7, 30, tzinfo=UTC))["KR"] == "2026-09-29"


def test_추세_필터_설정_모양이_틀리면_말한다() -> None:
    """`enabled: "false"` 를 말없이 켜짐으로 읽었다 (docs/infra.md 25.629, 감사)."""
    import json

    from batch.services import trend as tr

    def 클라(값: object):
        class C:
            def execute(self, sql, args=None):  # noqa: ANN001, ANN202
                from batch.core.turso import ResultSet

                return ResultSet(columns=["value"], rows=[(json.dumps(값),)], last_insert_rowid=None)

        return C()

    assert any("enabled" in w for w in tr.load_settings(클라({"enabled": "false"}))["warnings"])
    assert any("읽지 못해" in w for w in tr.load_settings(클라([1, 2]))["warnings"])
    assert tr.load_settings(클라({"enabled": False, "bear_factor": 0.5}))["warnings"] == []
