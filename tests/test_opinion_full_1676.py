"""37회차 C 전제 ② — 종목마다 첫 365일 의견 수집, 잘린 창 쪼개기, 이어 받음 (docs/factors.md 12.40, docs/infra.md 25.1676)."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from batch.jobs import kis_flows as job
from batch.sources import kis

열림 = job.OPINION_FULL_FROM


def _c(covered: list[int], truncated: list[int] = (), full: list[int] = ()) -> dict:  # type: ignore[assignment]
    return {"covered_ids": list(covered), "truncated_ids": list(truncated), "full_ids": list(full)}


def test_365일로_받아_잘리지_않은_종목만_full() -> None:
    c = job.opinion_coverage([1, 2, 3, 4], {1: 5, 2: kis.OPINION_PAGE_ROWS, 4: 0}, {1, 2, 3})
    # 2 는 잘림, 3 은 실패 — 365일을 받았다고 하지 않는다. 4 는 60일 창
    assert c["full_ids"] == [1] and c["failed_ids"] == [3] and c["truncated_ids"] == [2]
    assert "full_ids" not in job.opinion_coverage([1], {1: 0})  # 예전 꼴 그대로


def test_이어_받음은_실패·잘림·끊김에서_풀린다() -> None:
    d = 열림
    이력 = [(str(d), _c([1, 2, 3], full=[1, 2, 3])),
            (str(d + timedelta(1)), _c([1, 3], [3])),  # 2 실패, 3 잘림
            (str(d + timedelta(2)), _c([1, 2, 3], full=[2]))]  # fmt: skip
    assert job.opinion_continuous(이력, d + timedelta(3)) == {1, 2}
    # 앞 실행과 60일 넘게 떨어지면 60일 창이 앞 창에 닿지 않는다 — 모두 끊김
    끊긴 = [*이력, (str(d + timedelta(2 + job.OPINION_DAYS + 1)), _c([1, 2, 3]))]
    assert job.opinion_continuous(끊긴, d + timedelta(2 + job.OPINION_DAYS + 1)) == set()
    # 마지막 실행이 오늘과 그만큼 떨어져도
    assert job.opinion_continuous(이력, d + timedelta(2 + job.OPINION_DAYS + 1)) == set()
    assert job.opinion_continuous(이력, d + timedelta(2 + job.OPINION_DAYS)) == {1, 2}
    assert job.opinion_continuous([], d) == set()


def test_고를_종목은_여는_날부터_이어_오지_않은_것을_상한까지() -> None:
    assert job.full_fetch_ids([1, 2, 3], [], 열림 - timedelta(1)) == set()
    이력 = [(str(열림), _c([1, 2, 3], full=[2]))]
    assert job.full_fetch_ids([5, 1, 2, 3], 이력, 열림 + timedelta(1)) == {5, 1, 3}
    assert job.full_fetch_ids([5, 1, 2, 3], 이력, 열림 + timedelta(1), cap=2) == {5, 1}


def test_이력_읽기는_깨진_기록을_건너뛴다() -> None:
    rows = [("2026-11-04", json.dumps({"opinion_coverage": _c([1], full=[1])})), ("2026-11-05", "{깨짐"),
            ("2026-11-06", json.dumps({"opinion_coverage": "x"})), ("2026-11-07", None)]  # fmt: skip
    assert job.coverage_history(rows) == [("2026-11-04", _c([1], full=[1]))]


class _QC:
    """창 길이가 `넘침` 일보다 길면 100행(잘림), 아니면 날마다 한 행."""

    def __init__(self, 넘침: int) -> None:
        self.넘침, self.부름 = 넘침, []

    def opinions_with_count(self, code: str, since: date, until: date) -> tuple[list[dict], int]:
        self.부름.append((since, until))
        일수 = (until - since).days + 1
        if 일수 > self.넘침:
            return [{"date": str(until)}] * 3, kis.OPINION_PAGE_ROWS
        return [{"date": str(since + timedelta(i))} for i in range(일수)], 일수


def test_잘린_창은_반씩_쪼개_겹치지_않게_다시_받는다() -> None:
    qc = _QC(넘침=95)
    since, until = date(2025, 11, 4), date(2026, 11, 4)
    읽음, n = job.opinions_split(qc, "005930", since, until)  # type: ignore[arg-type]
    assert n < kis.OPINION_PAGE_ROWS
    assert sorted(r["date"] for r in 읽음) == [str(since + timedelta(i)) for i in range((until - since).days + 1)]
    잎 = [w for w in qc.부름 if (w[1] - w[0]).days + 1 <= 95]
    assert 잎[0][0] == since and 잎[-1][1] == until
    assert all(b[0] == a[1] + timedelta(1) for a, b in zip(잎, 잎[1:], strict=False))  # 이어지고 겹치지 않음


def test_깊이_상한에서도_잘리면_잘림으로_남긴다() -> None:
    qc = _QC(넘침=5)
    _읽음, n = job.opinions_split(qc, "005930", date(2025, 11, 4), date(2026, 11, 4))  # type: ignore[arg-type]
    assert n == kis.OPINION_PAGE_ROWS and len(qc.부름) == 2 ** (job.OPINION_SPLIT_DEPTH + 1) - 1


class _Rs:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def scalar(self):  # noqa: ANN201
        return self.rows[0][0]


class _Client:
    def __init__(self, 이력: list) -> None:
        self.이력, self.묶음 = 이력, []

    def execute(self, sql: str, args=None):  # noqa: ANN001, ANN201
        if sql == job.OPINION_HISTORY_SQL:
            assert args == [job.JOB_NAME]
            return _Rs(self.이력)
        if "kr_opinions" in sql:
            return _Rs([(1,)])  # 표가 비지 않았다
        return _Rs([])

    def batch(self, statements: list) -> None:
        self.묶음 += statements

    def close(self) -> None:
        pass


@pytest.mark.parametrize("오늘", [열림 - timedelta(1), 열림])
def test_실행은_고른_종목만_365일로_받고_기록에_남긴다(monkeypatch: pytest.MonkeyPatch, 오늘: date) -> None:
    이력 = [(str(오늘 - timedelta(1)), json.dumps({"opinion_coverage": _c([1, 2], full=[1])}))]
    c = _Client(이력)
    창: dict[str, int] = {}
    끝: dict = {}

    class QC:
        calls = 0

        def __init__(self, token: str) -> None:
            pass

        def opinions_with_count(self, code, since, until):  # noqa: ANN001, ANN201
            창[code] = (until - since).days
            return [], 0

        def investor(self, code):  # noqa: ANN001, ANN201
            return {}

        def short(self, code, a, b):  # noqa: ANN001, ANN201
            return {}

        def credit(self, code, d):  # noqa: ANN001, ANN201
            return {}

    monkeypatch.setattr(job, "TursoClient", lambda: c)
    monkeypatch.setattr(job.db, "apply_migrations", lambda _c: None)
    monkeypatch.setattr(job.db, "start_batch_run", lambda *a, **k: 1)
    monkeypatch.setattr(job.db, "finish_batch_run", lambda *a, **k: 끝.update(k))
    monkeypatch.setattr(job.db, "record_api_call", lambda *a, **k: None)
    monkeypatch.setattr(job.db, "now_iso", lambda: "2026-11-04T09:00:00+00:00")
    monkeypatch.setattr(job.cal, "user_today", lambda: 오늘)
    monkeypatch.setattr(job.cal, "is_session", lambda *_a: False)
    monkeypatch.setattr(job.kis, "configured", lambda: True)
    monkeypatch.setattr(job.kis, "access_token", lambda _c: ("t", False))
    monkeypatch.setattr(job.kis, "QuoteClient", QC)
    monkeypatch.setattr(job, "targets", lambda _c: [(1, "000001"), (2, "000002"), (3, "000003")])
    monkeypatch.setattr(job, "collect_events", lambda *a: [])
    assert job.run() == 0
    덮음 = 끝["step_log"]["opinion_coverage"]
    if 오늘 < 열림:  # 열기 전에는 예전 그대로 — 모두 60일
        assert set(창.values()) == {job.OPINION_DAYS} and 덮음["full_ids"] == []
    else:  # 1 은 이어 받았다 — 60일, 2·3 은 365일
        assert 창 == {"000001": job.OPINION_DAYS, "000002": job.OPINION_FIRST_DAYS, "000003": job.OPINION_FIRST_DAYS}
        assert 덮음["full_ids"] == [2, 3]
    assert 덮음["full_since"] == str(오늘 - timedelta(job.OPINION_FIRST_DAYS))
