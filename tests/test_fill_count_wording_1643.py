"""25.1643 — 가상모의투자 계좌 줄의 체결 수는 "이번 실행" 체결이다.

아침 실행이 판정하는 체결은 전 거래일 장의 것이라 "오늘 체결 n건" 은 틀린 말이었다(25.1631 감사 [권고]).
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from tests.test_paper_job_1445 import Client, 설정


def test_계좌_줄은_이번_실행_체결이라고_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=("A",))
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')")  # fmt: skip
    monkeypatch.setattr(cal, "local_today", lambda _m=None: date(2026, 10, 15))
    줄, _ = job.run_step(c, market="KR", trade_date="2026-10-14", late=False, signal_rows=[], settings=설정,
                        price_series={}, series_unknown={}, allocate=False,
                        now=datetime.fromisoformat("2026-10-14T23:30:00+00:00"))  # fmt: skip
    # 25.1721 — 머리 줄이 "이번 실행 체결" 을 밝히고 계좌 줄은 "체결 n건"
    assert 줄[0] == "🧪 가상모의투자 (평가 · 입금 대비 손익 · 이번 실행 체결)"
    계좌줄 = [x for x in 줄 if x.startswith("  가치 ")]
    assert 계좌줄 and 계좌줄[0].endswith(" · 체결 0건")
    assert all("오늘 체결" not in x for x in 줄)
