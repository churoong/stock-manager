"""주문 근거 근거표 꼴 점검(25.2005, conftest `_주문_근거는_근거표_꼴이다`)이 살아 있는가 (25.2006, 25.2005 교차검증 [권고]).

고정물은 `tests.test_paper_job_1445` 모듈이 없으면 조용히 건너뛴다 — 파일 이름이 바뀌거나 Client 가 옮겨지면 그물이
소리 없이 사라진다. 그물 자체를 여기서 본다"""

from __future__ import annotations

import json
import sys

import pytest

from batch.services import sell_flags as sf
from tests import conftest
from tests.conftest import 근거표_어긋남
from tests.test_paper_job_1445 import Client


def _주문(c: Client, oid: int, 근거: dict) -> None:
    c.conn.execute(
        "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency, decided_on,"
        " fill_session, tranche, reason, rationale_json, rule_version, created_at)"
        " VALUES (?, 'B', 'KR', 1, 'sell', 'market', 1, 'KRW', '2026-10-29', '2026-10-30', ?, 't', ?, 1, 't')",
        [oid, oid, json.dumps(근거, ensure_ascii=False)],  # 유일 키가 회차로 갈리게
    )


def test_그물은_옛_꼴만_잡고_근거표_꼴은_지난다(request: pytest.FixtureRequest) -> None:
    # 그물이 켜져 있다 — 고정물이 이 시험에도 붙고(autouse), 고정물이 보는 모듈이 Client 의 모듈이다(25.2007)
    assert "_주문_근거는_근거표_꼴이다" in request.fixturenames
    assert Client.__module__ == conftest.그물_모듈
    c = Client(accounts=("B",))
    모듈 = sys.modules[conftest.그물_모듈]
    assert any(x is c for x in 모듈.만든_client)  # 고정물이 볼 목록에 올랐다
    새 = sf._criterion("판정 종가", "8,700원", "손절 -3%", "prices", "2026-10-31")
    _주문(c, 1, {"flags": [{"criteria": [새]}], "signal": {"criteria": [새]}})
    _주문(c, 2, {"flags": [{"criteria": [{"name": "진입가", "value": 10000, "date": "2026-10-29"}]}]})
    _주문(c, 3, {"signal": {"criteria": "깨짐"}})
    어긋남 = 근거표_어긋남(c.conn)
    assert [x.split(":")[0] for x in 어긋남] == ["주문 2 flags[0]", "주문 3 signal"]
    # 고정물의 몸통(`그물_점검`)이 그 어긋남으로 실제로 깨지고 목록을 비운다(25.2007 — 몸통이 일찍 끝나도 잡게)
    with pytest.raises(AssertionError, match="주문 2 flags"):
        conftest.그물_점검()
    assert not 모듈.만든_client


def test_닫힌_연결은_통과로_삼키지_않는다() -> None:
    import sqlite3

    c = Client(accounts=("B",))
    c.conn.close()
    try:
        with pytest.raises(sqlite3.ProgrammingError):
            근거표_어긋남(c.conn)
    finally:
        sys.modules[conftest.그물_모듈].만든_client.remove(c)  # 고정물이 닫힌 DB 를 다시 읽지 않게


def test_열_이름이_바뀐_DB_는_통과로_삼키지_않는다() -> None:
    """표가 없는 DB 만 빈 답 — 열 이름이 바뀌면(no such column) 드러낸다(25.2007)"""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE paper_orders (id INTEGER)")
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        근거표_어긋남(conn)
    assert 근거표_어긋남(sqlite3.connect(":memory:")) == []
