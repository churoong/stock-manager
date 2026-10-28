"""같은 날 사고파는 계좌(당일 단타 T)의 체결 순서 (docs/paper.md 16.3.2, docs/infra.md 25.1881).

한 날 안에서 매도를 매수보다 먼저 읽으면 같은 날 산 것을 그날 판 T 의 장부가 "보유 없는 매도" 로 던져 단계 전체가
멈췄다. T 의 매도만 그날 매수 뒤로 — 다른 계좌의 순서는 바이트 그대로.
"""

from __future__ import annotations

import inspect
import random

import pytest

from batch.jobs import paper as job
from batch.services import paper as pp


def _체결(oid: int, aid: str, sid: int, side: str, day: str, q: int, px: float) -> pp.Fill:
    gross = px * q
    fee = gross * 0.00015
    cash = -(gross + fee) if side == "buy" else gross - fee - gross * 0.002
    return pp.Fill(oid, aid, sid, side, day, px, q, "KRW", 1.0, fee, 0.0 if side == "buy" else gross * 0.002, 0.0,
                   round(cash), horizon="short")  # fmt: skip


def _옛_열쇠(f: pp.Fill) -> tuple:
    return (f.fill_date, 0 if f.side == "sell" else 1, f.order_id)


def test_다른_계좌의_순서는_예전과_같다() -> None:
    rnd = random.Random(1881)
    for _ in range(300):
        fills = []
        보유: dict[tuple[str, int], int] = {}
        for oid in range(1, 40):
            aid, sid = rnd.choice(["A", "B", "H", "S", "R1"]), rnd.randint(1, 4)
            day = f"2026-10-{rnd.randint(1, 20):02d}"
            side = "sell" if 보유.get((aid, sid)) and rnd.random() < 0.4 else "buy"
            fills.append(_체결(oid, aid, sid, side, day, rnd.randint(1, 5), rnd.uniform(90, 110)))
            if side == "buy":
                보유[(aid, sid)] = 보유.get((aid, sid), 0) + 1
        assert sorted(fills, key=pp.fill_key) == sorted(fills, key=_옛_열쇠)


def test_T_는_같은_날_산_것을_그날_판다() -> None:
    살 = _체결(1, "T", 7, "buy", "2026-11-05", 10, 10_000)
    팔 = _체결(2, "T", 7, "sell", "2026-11-05", 10, 10_200)
    assert pp.positions([팔, 살]) == {}  # 예전 순서면 "보유 없는 매도" 로 던졌다
    실현 = pp.realized([팔, 살])
    assert len(실현) == 1 and 실현[0]["order_id"] == 2 and 실현[0]["pnl_krw"] == pytest.approx(
        팔.cash_krw + 살.cash_krw, abs=1)
    with pytest.raises(ValueError, match="보유 없는 매도"):
        pp.positions([_체결(2, "B", 7, "sell", "2026-11-05", 10, 10_200), _체결(1, "B", 7, "buy", "2026-11-05", 10, 1)])


def test_체결_판정도_같은_순서() -> None:
    assert pp.side_rank("B", "sell") == 0 < pp.side_rank("B", "buy") == 1 == pp.side_rank("T", "buy") < pp.side_rank(
        "T", "sell") == 2  # fmt: skip
    assert "pp.side_rank(" in inspect.getsource(job.fill_orders)
    assert pp.SAME_DAY_IDS == ("T",)


# ── 25.1882 (25.1881 교차검증 [권고]) — 판정 순서를 글자가 아니라 동작으로 ─────────────────────────


def test_판정_기존_계좌는_한_장_안에서_매도가_먼저() -> None:
    """먼저 낸 매수(주문 번호 작음)와 나중 낸 매도가 같은 장 — 매도가 먼저 판정된다(그날 판 돈을 그날 매수가 쓴다)"""
    from tests.test_paper_fill_1620 import 시세, 주문, 판정
    from tests.test_paper_job_1445 import Client

    c = Client()
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 1000000, 'deposit', 't', 'k')")  # fmt: skip
    주문(c, side="buy", limit=10_000, q=4, decided="2026-10-12", session="2026-10-13")
    시세(c, 1, "2026-10-13", 10_000, 9_900)
    판정(c, "KR", "2026-10-13")
    산 = 주문(c, side="buy", limit=10_000, q=2, decided="2026-10-14", session="2026-10-15", tranche=2)
    판 = 주문(c, side="sell", typ="market", q=4, decided="2026-10-14", session="2026-10-15", tranche=0)
    시세(c, 1, "2026-10-15", 9_900, 9_800)
    판정(c, "KR", "2026-10-15")
    줄 = [r[0] for r in c.q("SELECT order_id FROM paper_fills WHERE fill_date = '2026-10-15' ORDER BY id")]
    assert 줄 == [판, 산]


def test_판정_T_는_짝_매도가_주문_번호가_작아도_매수_뒤() -> None:
    """같은 장에 T 의 매도(번호 작음, 종가 판정)와 매수(시가) — 둘 다 차고 하루 끝 보유 0, 매도 실현손익이 남는다"""
    from tests.test_paper_fill_1620 import 시세, 주문, 판정
    from tests.test_paper_job_1445 import Client

    c = Client()
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 1000000, 'deposit', 't', 'k')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n, selection)"
                   " VALUES ('T', 'T', 'day_close', 1, 't', 'open', 10, 'short')")  # 계좌 행 — 시험이 직접  # fmt: skip
    판 = 주문(c, aid="T", side="sell", typ="market", q=5, session="2026-10-15", basis="close", tranche=0)
    산 = 주문(c, aid="T", side="buy", typ="market", q=5, session="2026-10-15", ref=10_000)
    시세(c, 1, "2026-10-15", 10_000, 10_300)
    새, _기록 = 판정(c, "KR", "2026-10-15")
    assert c.q("SELECT order_id, price FROM paper_fills WHERE account_id = 'T' ORDER BY id") == [(산, 10_000), (판, 10_300)]
    fills, _ = job._fills(c)
    assert pp.positions([f for f in fills if f.account_id == "T"]) == {}
    assert c.q("SELECT realized_krw IS NOT NULL FROM paper_fills WHERE order_id = ?", [판]) == [(1,)]
