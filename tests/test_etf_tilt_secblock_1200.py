"""SEC 가 도중에 막힌 달에 알파벳 앞쪽 일부만으로 순위를 쓰고 나머지 지난달 순위를 지웠다 (docs/infra.md 25.1200, ETF 감사 재현)."""

from __future__ import annotations

import json

import pytest

from batch.core import db
from batch.jobs import etf_tilt as job
from batch.sources import sec_edgar, sec_nport
from tests.test_portfolio_job import MemClient

ETFS = ["AAA", "BBB", "CCC", "DDD", "VTI"]


def _mem() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    for i, sym in enumerate(ETFS, start=1):
        c.execute("INSERT INTO etfs (id, symbol, country, name, source, fetched_at) VALUES (?, ?, 'US', ?, 't', 't')",
                  [i, sym, f"{sym} Fund"])  # fmt: skip
        c.execute("INSERT INTO etf_profiles (etf_id, as_of_date, category, currency, total_assets, stock_position,"
                  " source, fetched_at) VALUES (?, '2026-10-01', 'Large Blend', 'USD', 5e9, 0.99, 't', 't')", [i])  # fmt: skip
        옛 = {"criteria": [], "tilt": {"rank_all": i, "rank_all_size": 5, "group": "us"}}
        c.execute("INSERT INTO etf_picks (etf_id, as_of_date, bucket, passed, rationale_text, rationale_data,"
                  " calc_version, created_at) VALUES (?, '2026-10-01', '미국 주식', 1, 'x', ?, 5, 't')",
                  [i, json.dumps(옛)])  # fmt: skip
    for k in range(20):
        c.execute("INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at)"
                  " VALUES (?, ?, 'NYSE', 'US', ?, 'USD', 'active', 't', 't')", [100 + k, f"S{k}", f"S{k}"])  # fmt: skip
        c.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, calc_version, weights_json,"
                  " sentiment_weight_used, created_at) VALUES (?, '2026-10-10', 50, ?, 1, '{}', 0, 't')",
                  [100 + k, json.dumps({"value": 90 - k * 3, "quality": 60})])  # fmt: skip
    return mem


def test_SEC_가_막히면_미국_순위를_쓰지도_지우지도_않고_기준을_먼저_받는다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _mem()
    불림: list[str] = []

    class FakeSec:
        calls = 0

    class FakeNport:
        def __init__(self, sec) -> None:  # noqa: ANN001
            pass

        def fund_tickers(self):  # noqa: ANN202
            return {s: ("0000000001", f"S{s}") for s in ETFS}

        def ftd_cusips(self, _d):  # noqa: ANN001, ANN202
            return {f"C{k:08d}": f"S{k}" for k in range(20)}

        def latest(self, cik, series):  # noqa: ANN001, ANN202
            불림.append(series[1:])
            if series == "SCCC":
                raise sec_edgar.SecBlocked("HTTP 429")
            return sec_nport.NportDoc("0001-26-000001", "2026-08-29", "2026-06-30",
                                      [sec_nport.Holding("x", f"C{k:08d}", "", 5.0) for k in range(20)])  # fmt: skip

    monkeypatch.setattr(job.sec_edgar, "SecClient", lambda: FakeSec())
    monkeypatch.setattr(job.sec_nport, "NportClient", FakeNport)
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    assert job.run() == 0
    assert 불림[0] == "VTI"  # 기준을 맨 앞에
    for i, sym in enumerate(ETFS, start=1):
        t = json.loads(mem.conn.execute("SELECT rationale_data FROM etf_picks WHERE etf_id = ?", [i]).fetchone()[0])["tilt"]
        assert t["rank_all"] == i and t["rank_all_size"] == 5, sym  # 지난달 값 그대로(부분 순위로 덮지도, 지우지도 않음)
    상태, 글 = mem.conn.execute("SELECT status, error_text FROM batch_runs WHERE job_name = 'etf_tilt'").fetchone()
    assert 상태 == "partial" and "2개: CCC, DDD" in 글 and "지난달 값을 그대로" in 글


def test_장기_신호_비중은_그날_가장_새_판만_센다() -> None:
    """같은 날 판이 둘이면 새 판이 걸러 낸 종목의 옛 판 행까지 세었다 (docs/infra.md 25.1202, ETF 감사 #4)."""
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    for sid in (1, 2):
        c.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
                  " VALUES (?, ?, 'NYSE', 'US', 'USD', 'active', 't', 't')", [sid, f"T{sid}"])  # fmt: skip
    for sid, ver in ((1, 1), (2, 1), (1, 2)):  # 판 2 는 종목 1 만 냈다
        c.execute("INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
                  " tranche_plan, size_reduction, rationale_text, rationale_data, calc_version, created_at)"
                  " VALUES (?, '2026-10-10', 'long', 'x', 1, 2, 'USD', '[]', 1, 'x', '{}', ?, 't')", [sid, ver])  # fmt: skip
    assert job.load_long_signals(mem, "US") == ({1}, "2026-10-10")  # type: ignore[arg-type]


@pytest.mark.parametrize("어디", ["목록", "받기"])
def test_SEC_목록_실패나_망_오류도_실행을_죽이지_않는다(monkeypatch: pytest.MonkeyPatch, 어디: str) -> None:
    """펀드 티커 목록의 429·N-PORT 받기 중 연결 끊김이 `run()` 밖으로 올라가 국내 순위까지 못 썼다 (docs/infra.md 25.1280)."""
    mem = _mem()

    class FakeSec:
        calls = 0

    class FakeNport:
        def __init__(self, sec) -> None:  # noqa: ANN001
            pass

        def fund_tickers(self):  # noqa: ANN202
            if 어디 == "목록":
                raise sec_edgar.SecBlocked("HTTP 429")
            return {s: ("0000000001", f"S{s}") for s in ETFS}

        def ftd_cusips(self, _d):  # noqa: ANN001, ANN202
            return {}

        def latest(self, cik, series):  # noqa: ANN001, ANN202
            raise ConnectionError("Connection reset by peer")

    monkeypatch.setattr(job.sec_edgar, "SecClient", lambda: FakeSec())
    monkeypatch.setattr(job.sec_nport, "NportClient", FakeNport)
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    assert job.run() == 0  # 예전엔 예외로 죽었다
    상태, 글 = mem.conn.execute("SELECT status, error_text FROM batch_runs WHERE job_name = 'etf_tilt'").fetchone()
    assert 상태 == "partial" and "지난달 값을 그대로" in 글


def test_새_달_행에는_지난_판정_순위를_옮겨_싣는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """달마다 판정이 순위 없는 새 날짜 행을 먼저 써 '지난달 값이 남는다' 가 성립하지 않았다 (docs/infra.md 25.1281)."""
    mem = _mem()
    c = mem.conn
    for i in range(1, len(ETFS) + 1):  # 이번 달 판정 — 순위 없는 새 행
        c.execute("INSERT INTO etf_profiles (etf_id, as_of_date, category, currency, total_assets, stock_position,"
                  " source, fetched_at) VALUES (?, '2026-11-02', 'Large Blend', 'USD', 5e9, 0.99, 't', 't')", [i])  # fmt: skip
        c.execute("INSERT INTO etf_picks (etf_id, as_of_date, bucket, passed, rationale_text, rationale_data,"
                  " calc_version, created_at) VALUES (?, '2026-11-02', '미국 주식', 1, 'x', '{\"criteria\": []}', 5, 't')",
                  [i])  # fmt: skip

    class FakeSec:
        calls = 0

    class FakeNport:
        def __init__(self, sec) -> None:  # noqa: ANN001
            pass

        def fund_tickers(self):  # noqa: ANN202
            raise sec_edgar.SecBlocked("HTTP 429")

        def ftd_cusips(self, _d):  # noqa: ANN001, ANN202
            return {}

    monkeypatch.setattr(job.sec_edgar, "SecClient", lambda: FakeSec())
    monkeypatch.setattr(job.sec_nport, "NportClient", FakeNport)
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    assert job.run() == 0
    for i in range(1, len(ETFS) + 1):
        t = json.loads(c.execute("SELECT rationale_data FROM etf_picks WHERE etf_id = ? AND as_of_date = '2026-11-02'",
                                 [i]).fetchone()[0])["tilt"]  # fmt: skip
        assert t["rank_all"] == i and t["carried_from"] == "2026-10-01"


def test_옮기는_순위는_직전_판정일_하나에서만(monkeypatch: pytest.MonkeyPatch) -> None:
    """지난달 순위가 없는 ETF 는 지지난달 순위를 옮겨 서로 다른 달의 순위가 섞였다 (docs/infra.md 25.1377, ETF 감사)."""
    mem = _mem()
    c = mem.conn
    옛 = c.execute("SELECT rationale_data FROM etf_picks WHERE etf_id = 1 AND as_of_date = '2026-10-01'").fetchone()[0]
    c.execute("UPDATE etf_picks SET rationale_data = '{\"criteria\": []}' WHERE etf_id = 1 AND as_of_date = '2026-10-01'")
    c.execute("INSERT INTO etf_picks (etf_id, as_of_date, bucket, passed, rationale_text, rationale_data, calc_version,"
              " created_at) VALUES (1, '2026-09-01', '미국 주식', 1, 'x', ?, 5, 't')", [옛])  # fmt: skip
    for i in range(1, len(ETFS) + 1):  # 이번 달 판정 — 순위 없는 새 행
        c.execute("INSERT INTO etf_profiles (etf_id, as_of_date, category, currency, total_assets, stock_position,"
                  " source, fetched_at) VALUES (?, '2026-11-02', 'Large Blend', 'USD', 5e9, 0.99, 't', 't')", [i])  # fmt: skip
        c.execute("INSERT INTO etf_picks (etf_id, as_of_date, bucket, passed, rationale_text, rationale_data,"
                  " calc_version, created_at) VALUES (?, '2026-11-02', '미국 주식', 1, 'x', '{\"criteria\": []}', 5, 't')",
                  [i])  # fmt: skip

    class FakeSec:
        calls = 0

    class FakeNport:
        def __init__(self, sec) -> None:  # noqa: ANN001
            pass

        def fund_tickers(self):  # noqa: ANN202
            raise sec_edgar.SecBlocked("HTTP 429")

        def ftd_cusips(self, _d):  # noqa: ANN001, ANN202
            return {}

    monkeypatch.setattr(job.sec_edgar, "SecClient", lambda: FakeSec())
    monkeypatch.setattr(job.sec_nport, "NportClient", FakeNport)
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    assert job.run() == 0
    첫 = json.loads(c.execute("SELECT rationale_data FROM etf_picks WHERE etf_id = 1 AND as_of_date = '2026-11-02'"
                             ).fetchone()[0])  # fmt: skip
    assert "tilt" not in 첫  # 지지난달(09-01) 순위는 옮기지 않는다 — 모름
    for i in range(2, len(ETFS) + 1):
        t = json.loads(c.execute("SELECT rationale_data FROM etf_picks WHERE etf_id = ? AND as_of_date = '2026-11-02'",
                                 [i]).fetchone()[0])["tilt"]  # fmt: skip
        assert t["carried_from"] == "2026-10-01"


def test_핵심을_못_넘은_넓은_지수는_broad(monkeypatch: pytest.MonkeyPatch) -> None:
    """규모 등으로 핵심에서 빠진 넓은 지수 ETF 가 "넓은 지수가 아닙니다" 경고를 받았다 (docs/infra.md 25.1283, ETF 감사 재현)."""
    mem = _mem()
    c = mem.conn
    c.execute("UPDATE etf_picks SET passed = 0 WHERE etf_id = 2")  # BBB — 넓은 지수(미국 주식)인데 규모 미달
    c.execute("UPDATE etf_picks SET passed = 0, bucket = NULL WHERE etf_id = 3")  # CCC — 넓은 지수 아님

    class FakeSec:
        calls = 0

    class FakeNport:
        def __init__(self, sec) -> None:  # noqa: ANN001
            pass

        def fund_tickers(self):  # noqa: ANN202
            return {s: ("0000000001", f"S{s}") for s in ETFS}

        def ftd_cusips(self, _d):  # noqa: ANN001, ANN202
            return {f"C{k:08d}": f"S{k}" for k in range(20)}

        def latest(self, cik, series):  # noqa: ANN001, ANN202
            return sec_nport.NportDoc("0001-26-000001", "2026-08-29", "2026-06-30",
                                      [sec_nport.Holding("x", f"C{k:08d}", "", 5.0) for k in range(20)])  # fmt: skip

    monkeypatch.setattr(job.sec_edgar, "SecClient", lambda: FakeSec())
    monkeypatch.setattr(job.sec_nport, "NportClient", FakeNport)
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    assert job.run() == 0
    풀 = {i: (json.loads(c.execute("SELECT rationale_data FROM etf_picks WHERE etf_id = ?", [i]).fetchone()[0])
              .get("tilt") or {}).get("pool") for i in (1, 2, 3)}  # fmt: skip
    assert 풀 == {1: "core", 2: "broad", 3: "wide"}
