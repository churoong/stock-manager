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
