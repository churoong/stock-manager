"""미국 분할 재수집 대기 종목은 가격 계열 지표·단기 판정·성과지표 재계산을 모른다로 둔다 (docs/infra.md 25.1311, 19회차 A2)."""

from __future__ import annotations

import inspect

from batch.core import db
from batch.jobs import metrics as metrics_job
from batch.jobs import scores as job
from batch.services import signals as sg
from tests.test_portfolio_job import MemClient
from tests.test_scores_job import _날짜, universe_row
from tests.test_signals import short_input


def test_기준일까지_감지된_분할만() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at)"
                     " VALUES (1, 'T', 'NYSE', 'US', 'USD', 't', 't')")  # fmt: skip
    mem.conn.execute("INSERT INTO adjust_refresh_queue (stock_id, detected_at, sample_date, ratio_before, ratio_after)"
                     " VALUES (1, '2026-10-08T22:00:00+00:00', '2026-10-08', 1.0, 1.0)")  # fmt: skip
    assert db.split_pending_us(mem, "2026-10-08") == {1}  # type: ignore[arg-type]
    assert db.split_pending_us(mem, "2026-10-07") == set()  # type: ignore[arg-type]
    assert db.split_pending_us(mem) == {1}  # type: ignore[arg-type]


def test_점수의_가격_계열_지표를_비운다() -> None:
    # 4:1 분할 절벽 — 앞 250일 400달러, 뒤 10일 100달러
    closes = [400.0] * 250 + [100.0] * 10
    series = {sid: (_날짜(len(closes)), closes, []) for sid in (1, 2)}
    inputs = job.build_inputs([universe_row(1, "NASDAQ"), universe_row(2, "NASDAQ")], {}, {}, series=series,
                              split_pending={1})  # fmt: skip
    by = {i.stock_id: i.metrics for i in inputs}
    assert by[2]["momentum_6m"] == -0.75 and by[2]["momentum_3m"] == -0.75  # 대기 아니면 거짓 절벽이 그대로
    assert by[1]["momentum_6m"] is None and by[1]["momentum_3m"] is None
    assert by[1].get("idio_volatility") is None


def test_단기_판정을_내지_않는다() -> None:
    inp = short_input(split_pending=True)
    passed, data = sg.short_term(inp)
    assert not passed
    assert "분할 재수집 대기" in str(sg.judgement("short", inp, data))
    assert sg.short_term(short_input())[0]


def test_성과지표는_지난_행을_둔다() -> None:
    src = inspect.getsource(metrics_job.compute_all)
    assert "if stock_id in 분할대기:" in src and "분할_건너뜀.append(stock_id)" in src
