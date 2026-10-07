"""계좌 전체 노출 — 보유 ETF 를 구성종목으로 펼친다 (docs/portfolio.md 8장, docs/infra.md 25.1002)."""

from __future__ import annotations

import pytest

from batch.core import db
from batch.jobs import etf_tilt as tilt_job
from batch.jobs import portfolio as pf_job
from batch.services import etf_tilt as tilt
from batch.services import portfolio as pf
from tests.test_portfolio_job import MemClient


def _pos(sid: int, value: float) -> pf.Position:
    return pf.Position(sid, 1, "KRW", 1, 1, 1, 1, "2026-01-02", None, market_value_krw=value)


INFO = {1: ("삼성전자", "반도체"), 2: ("SK하이닉스", "반도체"), 3: ("현대차", "자동차"), 10: ("KODEX 200", None),
        11: ("모르는 ETF", None)}  # fmt: skip


def test_직접과_ETF_속을_합치고_모르는_비중은_따로() -> None:
    # 직접 삼성전자 400 + ETF 10(600: 삼성 30%·하이닉스 10%·현대차 10%, 나머지 50% 모름) + 모르는 ETF 11(0 원은 건너뜀)
    out = pf.lookthrough([_pos(1, 400), _pos(10, 600), _pos(11, 0)], {10, 11}, {10: {1: 30, 2: 10, 3: 10}}, INFO)
    assert out is not None
    assert out["etf_pct"] == 60.0
    assert out["covered_pct"] == 50.0
    top = out["by_stock"][0]
    assert top == {"stock_id": 1, "name": "삼성전자", "direct_pct": 40.0, "via_pct": 18.0, "total_pct": 58.0}
    assert out["by_sector"] == {"반도체": 64.0, pf.LOOKTHROUGH_UNKNOWN: 30.0, "자동차": 6.0}
    assert out["unknown_etfs"] == []


def test_구성을_모르는_ETF는_통째로_모름() -> None:
    out = pf.lookthrough([_pos(1, 500), _pos(11, 500)], {11}, {}, INFO)
    assert out is not None and out["covered_pct"] == 0.0
    assert out["unknown_etfs"] == ["모르는 ETF"]
    assert out["by_sector"][pf.LOOKTHROUGH_UNKNOWN] == 50.0


def test_비중_합이_100을_넘으면_줄인다() -> None:
    out = pf.lookthrough([_pos(10, 100)], {10}, {10: {1: 80, 2: 40}}, INFO)
    assert out is not None and out["covered_pct"] == 100.0
    assert [r["total_pct"] for r in out["by_stock"]] == [pytest.approx(66.67), pytest.approx(33.33)]


def test_ETF가_없으면_펼치지_않는다() -> None:
    assert pf.lookthrough([_pos(1, 100)], set(), {}, INFO) is None


def test_잇기는_심볼을_정규화하고_음수는_뺀다() -> None:
    stocks = {"BRKB": (7, "BRK.B", "Berkshire"), "AAPL": (8, "AAPL", "Apple")}
    got = tilt.lookthrough_weights([("c1", 2.0), ("c2", 5.0), ("c2", 1.0), ("c3", 3.0), ("c4", -1.0)],
                                   {"c1": "BRK-B", "c2": "AAPL", "c4": "AAPL"}, stocks)  # fmt: skip
    assert got == {7: 2.0, 8: 6.0}


def _db() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    for sid, ticker, asset, name in ((1, "005930", "stock", "삼성전자"), (2, "000660", "stock", "SK하이닉스"),
                                     (10, "069500", "etf", "KODEX 200"), (11, "999999", "etf", "옛 ETF")):  # fmt: skip
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, source, fetched_at, asset_type, sector)"
            " VALUES (?, ?, 'KOSPI', 'KR', ?, 'KRW', 't', 't', ?, '반도체')",
            [sid, ticker, name, asset],
        )
    return mem


def test_보유_ETF_구성을_저장하고_판_ETF는_지운다() -> None:
    mem = _db()
    c = mem.conn
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " market_value_krw, updated_at) VALUES (10, 3, 'KRW', 1, 1, 1, 1, '2026-01-02', 600, 't')"
    )
    # 더는 들고 있지 않은 ETF 의 옛 행
    c.execute("INSERT INTO etf_lookthrough VALUES (11, 1, 50, '2026-09-01', 'x', 't', 't')")
    picks = [{"pick_id": 5, "country": "KR", "symbol": "069500"}]
    docs = {("kr", "069500"): tilt_job.Fetched([("005930", 30.0), ("000660", 10.0), ("123456", 5.0)],
                                               "KODEX 2.0 2026-10-06", "2026-10-06", "2026-10-06", "kodex")}  # fmt: skip
    market = tilt_job.Market({"005930": "005930", "000660": "000660"},
                             {"005930": (1, "005930", "삼성전자"), "000660": (2, "000660", "SK하이닉스")},
                             {}, None, None, None, set(), None, "069500")  # fmt: skip
    out = tilt_job.store_lookthrough(mem, picks, {5: ("kr", "069500")}, docs, {"kr": market})  # type: ignore[arg-type]
    assert out == {"held": 1, "written": 1, "missing": []}
    rows = c.execute("SELECT etf_stock_id, stock_id, weight_pct, as_of FROM etf_lookthrough ORDER BY stock_id").fetchall()
    assert rows == [(10, 1, 30.0, "2026-10-06"), (10, 2, 10.0, "2026-10-06")]

    # 포트폴리오 재계산이 읽어 펼친다
    stocks = {int(r[0]): {"name": r[1], "sector": r[2], "asset_type": r[3]}
              for r in c.execute("SELECT id, name_ko, sector, asset_type FROM stocks").fetchall()}  # fmt: skip
    warnings: list[str] = []
    got = pf_job._lookthrough(mem, [_pos(1, 400), _pos(10, 600)], stocks, warnings)  # type: ignore[arg-type]
    assert got is not None and warnings == []
    assert got["by_stock"][0]["total_pct"] == 58.0 and got["as_of"] == "2026-10-06"
