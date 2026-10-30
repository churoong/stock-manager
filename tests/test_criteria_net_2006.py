"""주문 근거 근거표 꼴 점검(25.2005, conftest `_주문_근거는_근거표_꼴이다`)이 살아 있는가 (25.2006, 25.2005 교차검증 [권고]).

고정물은 `tests.test_paper_job_1445` 모듈이 없으면 조용히 건너뛴다 — 파일 이름이 바뀌거나 Client 가 옮겨지면 그물이
소리 없이 사라진다. 그물 자체를 여기서 본다"""

from __future__ import annotations

import json
import sys

from batch.services import sell_flags as sf
from tests.conftest import 근거표_어긋남
from tests.test_paper_job_1445 import Client


def _주문(c: Client, oid: int, 근거: dict) -> None:
    c.conn.execute(
        "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency, decided_on,"
        " fill_session, tranche, reason, rationale_json, rule_version, created_at)"
        " VALUES (?, 'B', 'KR', 1, 'sell', 'market', 1, 'KRW', '2026-10-29', '2026-10-30', ?, 't', ?, 1, 't')",
        [oid, oid, json.dumps(근거, ensure_ascii=False)],  # 유일 키가 회차로 갈리게
    )


def test_그물은_옛_꼴만_잡고_근거표_꼴은_지난다() -> None:
    c = Client(accounts=("B",))
    assert "tests.test_paper_job_1445" in sys.modules
    모듈 = sys.modules["tests.test_paper_job_1445"]
    assert any(x is c for x in 모듈.만든_client)  # 고정물이 볼 목록에 올랐다
    새 = sf._criterion("판정 종가", "8,700원", "손절 -3%", "prices", "2026-10-31")
    _주문(c, 1, {"flags": [{"criteria": [새]}], "signal": {"criteria": [새]}})
    _주문(c, 2, {"flags": [{"criteria": [{"name": "진입가", "value": 10000, "date": "2026-10-29"}]}]})
    _주문(c, 3, {"signal": {"criteria": "깨짐"}})
    어긋남 = 근거표_어긋남(c.conn)
    assert [x.split(":")[0] for x in 어긋남] == ["주문 2 flags[0]", "주문 3 signal"]
    # 이 시험의 일부러 넣은 어긋남이 고정물을 깨지 않게 지운다
    c.conn.execute("DELETE FROM paper_orders")
    assert 근거표_어긋남(c.conn) == []


def test_닫힌_연결은_통과로_삼키지_않는다() -> None:
    import sqlite3

    import pytest

    c = Client(accounts=("B",))
    c.conn.close()
    with pytest.raises(sqlite3.ProgrammingError):
        근거표_어긋남(c.conn)
    sys.modules["tests.test_paper_job_1445"].만든_client.remove(c)  # 고정물이 닫힌 DB 를 다시 읽지 않게
