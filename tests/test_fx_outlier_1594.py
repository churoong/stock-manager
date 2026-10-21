"""환율 이상값 — 앞뒤 이웃 중앙값에서 10% 넘게 벗어난 날은 저장하지 않는다 (docs/infra.md 25.1594)."""

from __future__ import annotations

from datetime import date, timedelta

from batch.jobs import fx as job


def _줄(값: list[float]) -> list[tuple]:
    d0 = date(2026, 10, 1)
    return [("USDKRW", (d0 + timedelta(days=i)).isoformat(), v, "t", "t") for i, v in enumerate(값)]


def test_하루짜리_깨진_값만_뺀다() -> None:
    값 = [1380.0, 1385.0, 1390.0, 13.9, 1388.0, 1392.0, 1395.0]  # 넷째 날 ÷100
    남김, 경고 = job.drop_outliers(_줄(값))
    assert [r[2] for r in 남김] == [1380.0, 1385.0, 1390.0, 1388.0, 1392.0, 1395.0]
    assert 경고 and "2026-10-04 13.90" in 경고[0]


def test_큰_추세와_큰_하루는_그대로() -> None:
    # 5년치 채우기처럼 천천히 30% 오른 계열 — 창 전체 중앙값이면 양끝이 빠졌을 것
    값 = [1100.0 + i * 3 for i in range(120)]
    남김, 경고 = job.drop_outliers(_줄(값))
    assert len(남김) == 120 and not 경고
    # 하루 5% 급등(위기 날)은 거르지 않는다
    남김, 경고 = job.drop_outliers(_줄([1300.0, 1302.0, 1305.0, 1370.0, 1372.0, 1375.0, 1374.0]))
    assert len(남김) == 7 and not 경고


def test_셋_미만은_그대로() -> None:
    assert job.drop_outliers(_줄([1380.0, 13.8])) == (_줄([1380.0, 13.8]), [])


def test_짝수_창_절반이_깨져도_뒤집히지_않는다() -> None:
    """×100 둘 + 정상 둘 — 위 중앙값 하나면 깨진 값을 저장하고 정상을 뺐다(25.1596, 25.1594 반박 [필수]).
    기준점이 없으면 판단할 수 없어 둘 다 남기고(뒤집지 않음), 기준점이 있으면 깨진 쪽만 뺀다"""
    줄 = _줄([1400.0, 1401.0, 140000.0, 140100.0])
    남김, _ = job.drop_outliers(줄)
    assert {r[2] for r in 남김} >= {1400.0, 1401.0}  # 정상값을 빼지 않는다
    _남김, 경고 = job.drop_outliers(줄)
    assert 경고 and "판단 보류" in 경고[0]  # 깨진 값을 조용히 저장하지 않고 알린다(25.1597)
    남김, 경고 = job.drop_outliers(줄, anchor=1398.0)
    assert [r[2] for r in 남김] == [1400.0, 1401.0] and "저장하지 않음" in 경고[0]


def test_기준점이_있으면_깨진_값이_절반을_넘어도_거른다() -> None:
    줄 = _줄([1402.0, 1403.0, 1404.0, 14.0, 14.1, 14.2, 14.3, 14.4, 14.5, 1405.0])
    남김, _ = job.drop_outliers(줄, anchor=1400.0)
    assert [r[2] for r in 남김] == [1402.0, 1403.0, 1404.0, 1405.0]


def test_수집이_저장된_환율을_기준점으로_쓴다(monkeypatch) -> None:  # noqa: ANN001
    from batch.core import db
    from batch.sources import yfinance_src
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for i, v in enumerate([1390.0, 1391.0, 1392.0, 1393.0, 1394.0]):
        mem.conn.execute("INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', ?, ?, 't', 't')",
                         [f"2026-09-2{i}", v])  # fmt: skip
    bars = [yfinance_src.DailyBar(ticker="KRW=X", date=f"2026-10-0{i + 1}", open=v, high=v, low=v, close=v, volume=0)
            for i, v in enumerate([1400.0, 140000.0, 140100.0, 1401.0])]  # fmt: skip
    monkeypatch.setattr(yfinance_src, "fetch_daily_bars", lambda *a, **k: type("R", (), {"ok": True, "data": bars,
                                                                                         "source": "t", "error": None})())
    monkeypatch.setattr(db, "record_api_call", lambda *a, **k: None)
    n, 경고 = job.collect(mem)  # type: ignore[arg-type]
    assert n == 2 and 경고
    assert mem.conn.execute("SELECT date FROM fx_rates WHERE date >= '2026-10-01' ORDER BY date").fetchall() == [
        ("2026-10-01",), ("2026-10-04",)]



def test_진짜_큰_움직임은_사흘_이어지면_받아들이고_저장이_멈추지_않는다() -> None:
    """1200 → 1350(+12.5%) 로 올라 머묾 — 기준점 하나와 견주면 그 뒤 저장이 영구히 멈췄다(25.1597, 25.1596 반박 [필수] 1)."""
    남김, 경고 = job.drop_outliers(_줄([1201.0, 1350.0, 1352.0, 1351.0, 1353.0]), anchor=1200.0)
    assert [r[2] for r in 남김] == [1201.0, 1350.0, 1352.0, 1351.0, 1353.0]
    assert any("크게 움직였고 3일 이어져 받아들임" in x for x in 경고)
    # ÷100 이 사흘 이어져도 50% 넘게 다르면 받아들이지 않는다
    남김, _ = job.drop_outliers(_줄([1201.0, 12.0, 12.1, 12.0, 1203.0]), anchor=1200.0)
    assert [r[2] for r in 남김] == [1201.0, 1203.0]


def test_옛_저장값은_기준점으로_쓰지_않는다(monkeypatch) -> None:  # noqa: ANN001
    """5년치를 다시 채울 때 창 첫날보다 10일 넘게 옛 값을 기준점으로 쓰면 최근 몇 년치를 뺐다(25.1597, 반박 [필수] 2)."""
    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for i in range(5):
        mem.conn.execute("INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', ?, 1150, 't', 't')",
                         [f"2021-01-0{i + 1}"])  # fmt: skip
    assert job._anchor(mem, "2021-01-08") == 1150.0  # type: ignore[arg-type]
    assert job._anchor(mem, "2021-03-01") is None  # type: ignore[arg-type]
