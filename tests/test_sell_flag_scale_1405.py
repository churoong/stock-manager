"""잣대가 바뀐 날은 재무악화를 판정하지 못한 날 — 걸린 플래그를 풀지 않는다 (docs/infra.md 25.1405, 매도 플래그 감사)."""

from __future__ import annotations

from datetime import date

import pytest

from batch.core import db
from batch.jobs import portfolio as pf_job
from batch.jobs import sell_flags as job
from batch.services import sell_flags as sf
from tests.test_report_picks import SqliteClient
from tests.test_sell_flags import holding


class _C(SqliteClient):
    def close(self) -> None:
        pass


W = '{"value":20,"quality":20,"growth":20,"momentum":20,"risk":20}'


def test_기준점을_못_찾은_날은_재무악화를_잇는다() -> None:
    오늘 = date(2026, 10, 15)
    h = holding(score_now=47.0, score_now_date="2026-10-14", score_at_buy=None, score_scale_unknown=True)
    assert "재무악화" in sf.unjudged(h, 오늘)
    # 같은 잣대면(기준점이 있거나 판이 그대로면) 예전처럼 판정한다 — 풀릴 수 있다
    assert "재무악화" not in sf.unjudged(holding(score_now=47.0, score_now_date="2026-10-14"), 오늘)


def test_판_올린_날_걸린_재무악화가_풀리지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _C()
    monkeypatch.setattr(db, "apply_migrations", lambda client: None)
    monkeypatch.setattr(job, "TursoClient", lambda: c)
    monkeypatch.setattr(pf_job, "쓰는_중", lambda cl: False)
    k = c.conn
    k.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'KOSPI', 'KR', 'A', 'KRW', 'active', 't', 't')"
    )
    k.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, price_date, close, market_value, updated_at)"
        " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-07-01', 'long', '2026-10-14', 100, 1000, 't')"
    )
    k.execute(
        "INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
        " horizon, score_at_trade, snapshot_as_of, created_at, updated_at)"
        " VALUES (1, 'buy', '2026-07-01', 100, 10, 'KRW', 1, 'none', 'long', 72.0, '2026-07-01', 't', 't')"
    )

    def sc(d, v, ver):
        k.execute(
            "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
            " weights_json, calc_version, created_at) VALUES (1, ?, ?, '{}', 0, ?, ?, 't')",
            [d, v, W, ver],
        )

    sc("2026-07-01", 72.0, 10)
    sc("2026-10-13", 48.0, 10)
    monkeypatch.setattr(job.db, "now_iso", lambda: "2026-10-13T23:27:00+00:00")
    job.run(date(2026, 10, 14))
    assert k.execute("SELECT reason_code FROM sell_flags WHERE is_active = 1").fetchall() == [("재무악화",)]
    sc("2026-10-14", 47.0, 11)  # 판 올림
    job.run(date(2026, 10, 15))
    # 판이 오른 날 — 같은 잣대의 기준점이 없어 판정하지 못했다. 어제 플래그를 잇는다(예전엔 풀렸다)
    assert k.execute("SELECT reason_code FROM sell_flags WHERE is_active = 1").fetchall() == [("재무악화",)]
    assert k.execute("SELECT COUNT(*) FROM sell_flags WHERE resolved_at IS NOT NULL").fetchone()[0] == 0
    # 다음 날 — 같은 잣대 구간마다 이어 더한다(25.1412): 72→48(판 10) 24점 + 47→46(판 11) 1점 = 25점. 예전엔 새 판 첫
    # 점수(47)가 기준점이 되어 1점 하락으로 풀렸다
    sc("2026-10-15", 46.0, 11)
    monkeypatch.setattr(job.db, "now_iso", lambda: "2026-10-15T23:27:00+00:00")
    job.run(date(2026, 10, 16))
    assert k.execute("SELECT reason_code FROM sell_flags WHERE is_active = 1").fetchall() == [("재무악화",)]
    assert k.execute("SELECT COUNT(*) FROM sell_flags WHERE resolved_at IS NOT NULL").fetchone()[0] == 0


def test_같은_잣대_구간마다_이어_더한다() -> None:
    같음 = lambda a, b: a["v"] == b["v"]  # noqa: E731
    시작 = {"total_score": 80.0, "v": 1}
    이력 = [{"as_of_date": "2026-09-02", "total_score": 75.0, "v": 1},
            {"as_of_date": "2026-09-03", "total_score": 60.0, "v": 2},  # 바뀐 날 — 점프 15 는 뺀다
            {"as_of_date": "2026-09-04", "total_score": 50.0, "v": 2}]  # fmt: skip
    assert sf.chained_score_drop(시작, 이력, "2026-09-04", 같음) == (15.0, 1)  # 5 + 10
    # 같은 날 두 판이면 지금 잣대를 잇는 행을 고른다
    두판 = [{"as_of_date": "2026-09-02", "total_score": 58.0, "v": 2}, {"as_of_date": "2026-09-02", "total_score": 70.0, "v": 1},
            {"as_of_date": "2026-09-03", "total_score": 52.0, "v": 2}, {"as_of_date": "2026-09-04", "total_score": 50.0, "v": 2}]  # fmt: skip
    # 80→70(판 1) 10 + 70→52 바뀜(뺌) + 52→50 2 = 12
    assert sf.chained_score_drop(시작, 두판, "2026-09-04", 같음) == (12.0, 1)
    # 마지막 걸음이 잣대 바뀜이면(지금 잣대로 견준 쌍 없음) 모름
    assert sf.chained_score_drop(시작, 이력[:2], "2026-09-03", 같음) is None
    assert sf.chained_score_drop(시작, 이력, "2026-09-09", 같음) is None  # 지금 날 행이 없다
