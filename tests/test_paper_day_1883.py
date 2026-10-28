"""가상모의투자 당일 단타 계좌 T — 짝 매도 주문 (docs/paper.md 16.3.2, docs/infra.md 25.1883).

10월엔 계좌 행이 없다(11월 마이그레이션). 시험이 행을 넣어 본다: 단기 신호를 다음 장 시가에 사고 같은 장 종가에 판다,
하루 끝 보유 0, 늦은 실행엔 진입 안 함, 쉼 없음, 짝을 잃은 보유는 다음 장 시가, 그리고 다른 계좌의 행은 그대로.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import paper as pp
from batch.services import report_picks
from tests.test_paper_job_1445 import Client, _시세, _신호, 설정
from tests.test_paper_swing_1871 import _시세3, _중기


def _계좌(accounts: tuple[str, ...], t: bool, s: bool = False) -> Client:
    c = Client(accounts=accounts)
    for aid, rule in (("S", "swing_close"), ("T", "day_close")):
        if (aid == "T" and t) or (aid == "S" and s):
            c.conn.execute(
                "INSERT INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n,"
                " selection) VALUES (?, ?, ?, 1, '2026-10-14T00:00:00+00:00', 'open', 10, 'short')", [aid, aid, rule])
    c.conn.execute(
        "INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
        " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')"
    )
    return c


@pytest.fixture(autouse=True)
def _고정(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())


def _하루(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str, signals: bool = True,
         late: bool = False) -> tuple[list[str], dict]:  # fmt: skip
    다음 = cal.next_session("KR", date.fromisoformat(trade_date))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    return job.run_step(
        c, market="KR", trade_date=trade_date, late=late, signal_rows=[_신호(), _중기()] if signals else [],
        settings=설정, price_series={}, series_unknown={}, allocate=signals,
        now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"),
    )  # fmt: skip


def test_T_는_단기_신호를_시가에_사고_같은_장_종가에_파는_짝을_낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), t=True)
    _하루(c, monkeypatch, "2026-10-14")
    주문 = c.q("SELECT stock_id, side, order_type, fill_session, fill_basis, tranche, reason, rationale_json"
               " FROM paper_orders WHERE account_id = 'T' ORDER BY side")  # fmt: skip
    assert [r[:7] for r in 주문] == [(1, "buy", "market", "2026-10-15", "open", 1, "2부 배분"),
                                     (1, "sell", "market", "2026-10-15", "close", 0, "단타종가")]  # fmt: skip
    assert json.loads(주문[1][7])["rule"] == "day_v1"
    # 수량이 같다 — 산 만큼 판다
    assert len({q for (q,) in c.q("SELECT quantity FROM paper_orders WHERE account_id = 'T'")}) == 1


def test_T_는_하루_끝_보유가_0_이고_실현손익이_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), t=True)
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_000, 10_300)
    _시세3(c, "2026-10-15", 10_000)
    _, 기록 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    체결 = c.q("SELECT side, price, fill_date FROM paper_fills WHERE account_id = 'T' ORDER BY id")
    assert 체결 == [("buy", 10_000, "2026-10-15"), ("sell", 10_300, "2026-10-15")]
    fills, _ = job._fills(c)
    assert pp.positions([f for f in fills if f.account_id == "T"]) == {}
    assert 기록["accounts"]["T"]["recon_ok"] is True
    assert c.q("SELECT positions_json FROM paper_daily WHERE account_id = 'T' ORDER BY as_of DESC LIMIT 1") == [("[]",)]
    assert c.q("SELECT realized_krw > 0 FROM paper_fills WHERE account_id = 'T' AND side = 'sell'") == [(1,)]
    # 짝으로 이미 팔았으니 안전망 매도(단타잔여)는 없다
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'T' AND reason = ?", [pp.DAY_LEFT_CODE]) == [(0,)]


def test_T_는_늦은_실행에_진입하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), t=True)
    _, 기록 = _하루(c, monkeypatch, "2026-10-14", late=True)  # 장 열린 뒤 — 주문 기준 종가
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'T'") == [(0,)]
    assert 기록["accounts"]["T"]["buy"]["late_skip"] == 1
    assert c.q("SELECT kind, reason FROM paper_decisions WHERE account_id = 'T' AND stock_id = 1") == [
        ("skip", pp.DAY_LATE_PREFIX + "장 열린 뒤 실행")]
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'B'")[0][0] >= 1  # 다른 계좌는 그대로 낸다


def test_T_는_다시_사기_쉼이_없다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), t=True)
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_000, 10_300)
    _시세3(c, "2026-10-15", 10_000)
    _하루(c, monkeypatch, "2026-10-15")  # 판 다음 날 같은 단기 신호가 다시 온다
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'T' AND side = 'buy'") == [(2,)]


def test_짝을_잃은_보유는_다음_장_시가에_판다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), t=True)
    _하루(c, monkeypatch, "2026-10-14")
    c.conn.execute("DELETE FROM paper_orders WHERE account_id = 'T' AND side = 'sell'")  # 짝 행이 빠진 날
    _시세(c, "2026-10-15", 10_000, 10_300)
    _시세3(c, "2026-10-15", 10_000)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert c.q("SELECT reason, fill_session, fill_basis FROM paper_orders WHERE account_id = 'T' AND side = 'sell'") == [
        (pp.DAY_LEFT_CODE, "2026-10-16", "open")]


def _고름(c: Client, 표: str, r: tuple) -> tuple:
    키 = {x[0]: tuple(x[1:]) for x in c.q("SELECT id, account_id, decided_on, stock_id, side, tranche FROM paper_orders")}
    r = r[1:]
    if 표 == "paper_fills":
        r = (키[r[0]], *r[1:])
    if 표 == "paper_decisions":
        cols = [d[1] for d in c.conn.execute("PRAGMA table_info(paper_decisions)")][1:]
        i = cols.index("detail_json")
        d = json.loads(r[i]) if r[i] else None
        if isinstance(d, dict) and "order_id" in d:
            r = (*r[:i], json.dumps({**d, "order_id": 키[d["order_id"]]}, ensure_ascii=False), *r[i + 1:])
    return r


def test_T_가_있어도_다른_계좌의_행은_같다(monkeypatch: pytest.MonkeyPatch) -> None:
    """16.3.2 무변화 — T 는 id 순서 맨 끝. S 까지 있는 아홉+1 계좌로, 둘째 날에도 신호"""
    표들 = ("paper_orders", "paper_fills", "paper_decisions", "paper_daily")
    결과 = []
    for t in (False, True):
        c = _계좌(("A", "B", "C", "D", "E", "G", "H", "R1", "R2"), t=t, s=True)
        _하루(c, monkeypatch, "2026-10-14")
        _시세(c, "2026-10-15", 10_000, 9_650)
        _시세3(c, "2026-10-15", 10_000)
        _하루(c, monkeypatch, "2026-10-15")
        _시세(c, "2026-10-16", 9_700, 9_800)
        _시세3(c, "2026-10-16", 10_100)
        _하루(c, monkeypatch, "2026-10-16", signals=False)
        # 행 번호와 그것을 가리키는 주문 번호(체결의 order_id, 놓친 회차 결정의 detail.order_id)는 T 주문이 끼면 밀린다 —
        # 주문의 자연 키(계좌·판정일·종목·매매·회차)로 바꿔 견준다(25.1883)
        결과.append({x: sorted(_고름(c, x, r) for r in c.q(f"SELECT * FROM {x} WHERE account_id != 'T'")) for x in 표들})
        if t:
            assert c.q("SELECT COUNT(*) FROM paper_fills WHERE account_id = 'T'")[0][0] >= 2
    for x in 표들:
        assert 결과[0][x] == 결과[1][x], x
