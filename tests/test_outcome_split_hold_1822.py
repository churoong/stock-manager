"""신호 성적표는 미국 분할 재수집 대기 종목을 다시 채점하지 않고 지난 성적을 둔다 (docs/infra.md 25.1822).

일일 배치는 분할을 감지하면 최근 10일만 분할 반영 값으로 덮고, 그 앞은 토요일 재수집까지 분할 전 값이다. 성적표(월
21:07 UTC)가 그 계열을 그대로 채점하면 4:1 분할이 거짓 −74%·손절 도달로 저장되고 주간 사후 분석 텔레그램에 실렸다.
"""

from __future__ import annotations

import inspect
from datetime import date, timedelta

from batch.core import db
from batch.jobs import signal_outcomes as so
from tests.test_signal_outcomes_version import 메모리DB

오늘 = "2026-10-26"
기준 = "2026-09-14"


class _DB(메모리DB):
    def batch(self, statements):  # noqa: ANN001, ANN201
        return [self.execute(sql, args) for sql, args in statements]


def _장부() -> _DB:
    m = _DB()
    for sid in (1, 2):
        m.종목(sid, "US")
        m.신호(sid, 기준, "short", 1)
    m.conn.execute("UPDATE signals SET target_price = 110, stop_price = 93")
    d = date.fromisoformat(기준)
    날들: list[str] = []
    while len(날들) < 26:
        if d.weekday() < 5:
            날들.append(d.isoformat())
        d += timedelta(days=1)
    for i, 날 in enumerate(날들):
        값 = 100 + i * 0.2
        분할전 = i < len(날들) - 7  # 종목 1: 마지막 7일만 4:1 분할 반영(일일 배치의 최근 10일 덮기)
        for sid, 종가 in ((1, 값 / (1 if 분할전 else 4)), (2, 값)):
            m.conn.execute(
                "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
                " VALUES (?, ?, ?, ?, 'USD', 't', 't')",
                [sid, 날, 종가, 종가],
            )
    m.conn.execute(
        "INSERT INTO adjust_refresh_queue (stock_id, detected_at, sample_date, ratio_before, ratio_after)"
        " VALUES (1, '2026-10-26T13:00:00Z', '2026-10-23', 1.0, 1.0)"
    )
    # 지난주 실행이 저장한 종목 1 의 성적(분할 전 계열 그대로라 정상값)
    m.conn.execute(
        f"INSERT INTO signal_outcomes ({so._OUT_COLS}) VALUES (1, ?, 'short', ?, 100, 0.01, 0.04, NULL, 0.05,"
        " -0.01, 0, 0, NULL, NULL, 26, 't')",
        [기준, 기준],
    )
    return m


def test_대기_종목은_다시_채점하지_않고_지난_행을_집계에_넣는다() -> None:
    m = _장부()
    대기 = db.split_pending_us(m, 오늘)  # type: ignore[arg-type]
    assert 대기 == {1}
    results, stats = so.compute(m, "US", 오늘, held=대기)  # type: ignore[arg-type]
    assert [o.stock_id for o in results] == [2]
    so.store(m, "US", [so.to_row(o, "t") for o in results], stats, "t", so.since_of(오늘), held=대기)  # type: ignore[arg-type]
    남은 = dict(m.conn.execute("SELECT stock_id, ret_20d FROM signal_outcomes").fetchall())
    assert 남은[1] == 0.04 and 남은[2] is not None and 남은[2] > 0  # 거짓 −74% 가 아니다
    짧은20 = next(s for s in stats if s.horizon == "short" and s.window == 20)
    assert 짧은20.n == 2  # 지난 행이 집계에 든다
    # 대기를 보지 않으면(예전) 종목 1 이 −74% 로 채점됐다
    예전, _ = so.compute(m, "US", 오늘)  # type: ignore[arg-type]
    assert next(o for o in 예전 if o.stock_id == 1).rets[20] < -0.7


def test_실행은_미국에서_대기열을_읽어_넘긴다() -> None:
    글 = inspect.getsource(so.run)
    assert 'db.split_pending_us(client, today) if country == "US" else set()' in 글
    assert "compute(client, country, today, 계획, held=대기, shadow=(그림자, 비용, 그림자_메모))" in 글  # 25.1862
    assert "since_of(today), held=대기)" in 글
    # 점수 보정표에도 남긴 행이 든다 (25.1824, 교차검증 [권고] — 빼도 시험이 통과했다)
    assert "load_held(client, country, since_of(today), 대기)" in 글 and "calibrations(client, country, results + 남긴)" in 글
