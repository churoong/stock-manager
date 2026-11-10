"""ETF 전술 계좌(X·Y) 매수에는 가상 대조 분위를 채우지 않는다 (docs/infra.md 25.2218, 결함 루프 [참고]).

가상 대조는 같은 날 주식 유니버스 무작위 20종목 안 분위로 '선정 실력' 을 잰다(paper.md 10장). 지수·인버스 ETF 를 그
분위에 놓으면 오른 장엔 지수가 중간, 내린 장엔 인버스가 위에 놓일 뿐이라 성과 분해의 '가상 대조' 줄을 오도한다."""

from __future__ import annotations

import json

from batch.core import db
from batch.jobs import paper as jp
from batch.services import etf_tactical as et
from tests.test_desk_incidents_1571 import MemClient


def test_가상_대조_대상에서_ETF_전술_계좌를_뺀다() -> None:
    assert set(jp.NO_CONTROL_IDS) == set(et.ACCOUNTS) == {"X", "Y"}
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT OR IGNORE INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
              " VALUES (901, 'T901', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')")  # fmt: skip
    for aid in ("B", "X"):
        c.execute("INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ref_date, ret_20d,"
                  " created_at) VALUES (?, 'KR', '2026-11-10', 901, 'buy', 'r', '2026-11-09', 1.0, 't')", [aid])  # fmt: skip
    got = {r[0] for r in c.execute(
        "SELECT p.account_id FROM paper_decisions p WHERE p.id IN (SELECT id FROM (" + jp.PENDING_CONTROL_SQL + "))",
        ["KR", json.dumps(list(jp.NO_CONTROL_IDS)), 50]).fetchall()}  # fmt: skip
    assert got == {"B"}
