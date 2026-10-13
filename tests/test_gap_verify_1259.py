"""공백 위 큰 등락은 야후에 다시 물어 분할일 때만 분할로 적는다 (docs/infra.md 25.1259, 미국 시세 감사·교차검증)."""

from __future__ import annotations

import pytest

from batch.jobs import daily
from batch.sources import yfinance_src
from batch.sources.yfinance_src import DailyBar, FetchResult
from tests.test_gap_split_1247 import _저장소, _창, _평일


def _야후(monkeypatch: pytest.MonkeyPatch, 옛날_종가: float | None, ok: bool = True) -> list:
    """다시 물으면 공백 앞 날(09-04)을 이 종가로 돌려준다. None 이면 그날이 없다."""
    불림: list = []

    def fake(symbols, lookback_days=10, chunk_size=200):  # noqa: ANN001, ANN202
        불림.append((tuple(symbols), lookback_days))
        rows = [] if 옛날_종가 is None else [DailyBar("XYZ", "2026-09-04", 옛날_종가, adj_close=옛날_종가 * 0.98, volume=1)]
        return FetchResult(ok=ok, data=rows)

    monkeypatch.setattr(yfinance_src, "fetch_daily_bars", fake)
    return 불림


def _대기열(c) -> list:  # noqa: ANN001
    return c.conn.execute("SELECT ratio_before, ratio_after FROM adjust_refresh_queue WHERE stock_id = 1").fetchall()


def _분할기록(c) -> bool:  # noqa: ANN001
    return c.conn.execute("SELECT 1 FROM settings WHERE key = 'us_split_detections'").fetchone() is not None


def test_정지_뒤_진짜_폭락은_분할로_적지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    불림 = _야후(monkeypatch, 400.0)  # 옛 종가 그대로 — 분할 아님
    c = _저장소()
    말: list[str] = []
    daily._store_us_bars(c, _창("2026-10-09", 240.0), "2026-10-09", {"XYZ": 1}, "yfinance", detect_drift=True, notes=말)
    assert 불림 and 불림[0][0] == ("XYZ",) and 불림[0][1] >= 35  # 공백 앞 날까지 닿는 창
    assert _대기열(c) == [] and not _분할기록(c) and 말 == []


def test_공백_중_진짜_분할은_그대로_잡는다(monkeypatch: pytest.MonkeyPatch) -> None:
    _야후(monkeypatch, 100.0)  # 야후가 옛 날짜도 분할 반영해 돌려준다
    c = _저장소()
    daily._store_us_bars(c, _창("2026-10-09", 100.0), "2026-10-09", {"XYZ": 1}, "yfinance", detect_drift=True, notes=[])
    q = _대기열(c)
    assert len(q) == 1 and q[0][0] == q[0][1] and _분할기록(c)


@pytest.mark.parametrize(("종가", "ok"), [(None, True), (400.0, False)])
def test_확인을_못_하면_예전처럼_분할로_둔다(monkeypatch: pytest.MonkeyPatch, 종가, ok) -> None:  # noqa: ANN001
    _야후(monkeypatch, 종가, ok=ok)
    c = _저장소()
    daily._store_us_bars(c, _창("2026-10-09", 240.0), "2026-10-09", {"XYZ": 1}, "yfinance", detect_drift=True, notes=[])
    q = _대기열(c)
    assert len(q) == 1 and q[0][0] == q[0][1]


def test_창_내내_정지했다_그날_재개해도_분할을_잡는다(monkeypatch: pytest.MonkeyPatch) -> None:
    _야후(monkeypatch, 100.0)
    c = _저장소()
    오늘만 = [DailyBar("XYZ", "2026-10-09", 100.0, adj_close=98.0, volume=1000)]
    daily._store_us_bars(c, 오늘만, "2026-10-09", {"XYZ": 1}, "yfinance", detect_drift=True, notes=[])
    q = _대기열(c)
    assert len(q) == 1 and q[0][0] == q[0][1]


def test_그날_재개가_진짜_등락이면_조용하다(monkeypatch: pytest.MonkeyPatch) -> None:
    _야후(monkeypatch, 400.0)
    c = _저장소()
    오늘만 = [DailyBar("XYZ", "2026-10-09", 240.0, adj_close=235.2, volume=1000)]
    daily._store_us_bars(c, 오늘만, "2026-10-09", {"XYZ": 1}, "yfinance", detect_drift=True, notes=[])
    assert _대기열(c) == []


def test_평일_도우미() -> None:
    assert list(_평일("2026-10-09", "2026-10-12")) == ["2026-10-09", "2026-10-12"]
