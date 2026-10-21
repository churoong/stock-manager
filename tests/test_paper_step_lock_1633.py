"""가상모의투자 단계 — 시장 사이 임대 잠금과 실행 시작 뒤 입금 (docs/infra.md 25.1633, docs/paper.md 2.4).

실행 겹침 감사 [필수]: ① 국내·미국 단계가 겹치면 장부 현금이 음수(재현 −900,284원) ② 늦게 쓴 평가 행이 상대 체결을 못 본
채 덮어 다음 날 검산이 거짓으로 어긋남 ③ 실행 시작(`지금`) 뒤·입금 읽기 전 입금이 오늘과 내일 두 번 세여 시간가중 지수 반토막.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from tests.test_paper_job_1445 import Client, 설정


class _시계:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def 잠(self, s: float) -> None:
        self.t += timedelta(seconds=s)


def _값(c: Client) -> dict:
    return json.loads(c.q("SELECT value FROM settings WHERE key = ?", [job.STEP_LOCK_KEY])[0][0])


def test_한_시장이_잡으면_다른_시장은_기다렸다_못_잡으면_건너뛴다() -> None:
    c = Client(accounts=None)
    시계 = _시계(datetime(2026, 10, 21, 23, 30, tzinfo=UTC))
    with job.step_lock(c, "KR", clock=시계, sleep=시계.잠) as 국내:
        assert 국내 is not None and _값(c)["owner"] == "KR"
        with job.step_lock(c, "US", clock=시계, sleep=시계.잠) as 미국:
            assert 미국 is None  # 기다려도 국내가 놓지 않음
        assert 시계.t - datetime(2026, 10, 21, 23, 30, tzinfo=UTC) <= job.STEP_LOCK_WAIT + timedelta(seconds=30)
        assert _값(c)["token"] == 국내["token"]  # 못 잡은 쪽은 남의 잠금을 건드리지 않는다
    assert _값(c) == {}  # 놓음
    with job.step_lock(c, "US", clock=시계, sleep=시계.잠) as 미국:
        assert 미국 is not None and 미국["owner"] == "US"


def test_기다리는_동안_놓이면_잡는다() -> None:
    c = Client(accounts=None)
    시계 = _시계(datetime(2026, 10, 21, 23, 30, tzinfo=UTC))
    먼저 = job.step_lock(c, "KR", clock=시계, sleep=시계.잠)
    국내 = 먼저.__enter__()
    assert 국내 is not None

    def 잠(s: float) -> None:
        시계.잠(s)
        if 시계.t >= datetime(2026, 10, 21, 23, 31, tzinfo=UTC):
            먼저.__exit__(None, None, None)

    with job.step_lock(c, "US", clock=시계, sleep=잠) as 미국:
        assert 미국 is not None and 시계.t < datetime(2026, 10, 21, 23, 32, tzinfo=UTC)


def test_기한이_지난_잠금은_넘겨받고_옛_주인은_새_잠금을_놓지_않는다() -> None:
    c = Client(accounts=None)
    시계 = _시계(datetime(2026, 10, 21, 23, 30, tzinfo=UTC))
    끊긴 = job.step_lock(c, "KR", clock=시계, sleep=시계.잠)
    assert 끊긴.__enter__() is not None
    시계.t += job.STEP_LOCK_TTL + timedelta(seconds=1)  # 끊긴 실행 — 기한 지남
    with job.step_lock(c, "US", clock=시계, sleep=시계.잠) as 미국:
        assert 미국 is not None
        끊긴.__exit__(None, None, None)  # 늦게 돌아온 옛 주인이 놓아도
        assert _값(c)["token"] == 미국["token"]  # 새 주인의 잠금은 그대로


def _하루(c: Client, mp: pytest.MonkeyPatch, 오늘: str, trade_date: str, now: str) -> None:
    mp.setattr(cal, "local_today", lambda _m=None: date.fromisoformat(오늘))
    job.run_step(c, market="KR", trade_date=trade_date, late=False, signal_rows=[], settings=설정, price_series={},
                 series_unknown={}, allocate=False, now=datetime.fromisoformat(now))  # fmt: skip


def test_실행_시작_뒤_들어온_입금은_다음_실행이_센다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=("A",))
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')")  # fmt: skip
    _하루(c, monkeypatch, "2026-10-15", "2026-10-14", "2026-10-14T23:30:00+00:00")
    # 실행 시작(23:30:00) 0.2초 뒤·입금 읽기 전에 들어온 입금
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-16', 1000000, 'deposit', '2026-10-15T23:30:00.200000+00:00', 'k2')")  # fmt: skip
    _하루(c, monkeypatch, "2026-10-16", "2026-10-15", "2026-10-15T23:30:00+00:00")
    assert c.q("SELECT invested_krw FROM paper_daily WHERE as_of = '2026-10-16'") == [(1_000_000.0,)]  # 오늘은 빼고
    _하루(c, monkeypatch, "2026-10-17", "2026-10-16", "2026-10-16T23:30:00+00:00")
    행 = c.q("SELECT invested_krw, twr_index, recon_ok FROM paper_daily WHERE as_of = '2026-10-17'")[0]
    assert 행[0] == 2_000_000.0 and 행[1] == pytest.approx(1.0) and 행[2] == 1  # 내일 한 번만 — 지수 그대로


def test_같은_시장이_남긴_잠금은_기한_전에도_넘겨받는다() -> None:
    """교차검증 [필수] 2 — Actions `timeout` 으로 죽은 실행의 잠금이 같은 시장의 손 실행·예비 예약을 막았다."""
    c = Client(accounts=None)
    시계 = _시계(datetime(2026, 10, 21, 23, 30, tzinfo=UTC))
    죽은 = job.step_lock(c, "KR", clock=시계, sleep=시계.잠)
    assert 죽은.__enter__() is not None  # 놓지 못하고 죽음
    시계.t += timedelta(minutes=16)
    with job.step_lock(c, "KR", clock=시계, sleep=시계.잠) as 다시:
        assert 다시 is not None and 시계.t == datetime(2026, 10, 21, 23, 46, tzinfo=UTC)  # 기다리지 않음


def test_예외가_나도_잠금을_놓는다() -> None:
    c = Client(accounts=None)
    with pytest.raises(RuntimeError), job.step_lock(c, "KR"):
        raise RuntimeError("단계 실패")
    assert _값(c) == {}


def test_깨진_잠금_값은_빈_것으로_본다() -> None:
    """교차검증 [권고] — 손으로 넣은 JSON 아닌 값에 `json_extract` 가 오류를 내 단계가 날마다 실패할 뻔했다."""
    c = Client(accounts=None)
    c.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, 'not json', 't')", [job.STEP_LOCK_KEY])
    with job.step_lock(c, "KR") as 잠금:
        assert 잠금 is not None


def test_못_잡으면_단계를_건너뛰고_경고만(monkeypatch: pytest.MonkeyPatch) -> None:
    """교차검증 [필수] 3 — 못 잡았는데 단계를 돌리는 배선을 시험이 못 잡았다."""
    c = Client(accounts=None)
    시계 = _시계(datetime(2026, 10, 21, 23, 30, tzinfo=UTC))
    불림: list[str] = []
    monkeypatch.setattr(job, "run_step", lambda *_a, **_k: (불림.append("x"), (["줄"], {"ok": 1}))[1])
    원래 = job.step_lock
    monkeypatch.setattr(job, "step_lock", lambda cl, m: 원래(cl, m, clock=시계, sleep=시계.잠))
    with 원래(c, "US", clock=시계, sleep=시계.잠):
        경고: list[str] = []
        줄, 기록 = job.run_step_locked(c, "KR", 경고, trade_date="2026-10-21")
        assert 줄 == [] and 기록["skipped"].startswith("다른 시장") and 불림 == []
        assert 경고 and "가상모의투자 단계를 건너뜀" in 경고[0] and "3분" in 경고[0]
    경고 = []
    assert job.run_step_locked(c, "KR", 경고, trade_date="2026-10-21") == (["줄"], {"ok": 1}) and 불림 == ["x"]
    assert 경고 == [] and _값(c) == {}


def test_일일_배치는_잠금_안에서_단계를_부른다() -> None:
    import inspect

    from batch.jobs import daily

    본문 = inspect.getsource(daily)
    assert "paper_job.run_step_locked(" in 본문 and "paper_job.run_step(" not in 본문


def test_쥔_동안_종료_신호가_오면_놓고_끝난다() -> None:
    """교차검증 [필수] 2 — Actions `timeout` 의 SIGTERM 엔 처리기가 없어 finally 가 돌지 않고 잠금이 기한까지 남았다."""
    import os
    import signal

    c = Client(accounts=None)
    예전 = signal.getsignal(signal.SIGTERM)
    with pytest.raises(SystemExit) as 끝, job.step_lock(c, "KR"):
        os.kill(os.getpid(), signal.SIGTERM)
    assert 끝.value.code == 128 + signal.SIGTERM and _값(c) == {}
    assert signal.getsignal(signal.SIGTERM) == 예전  # 처리기는 잠금 밖에선 예전대로
