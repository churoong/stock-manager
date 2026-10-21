"""체결 판정 감사 [필수] 1·2·3 (docs/paper.md 2.3, docs/infra.md 25.1620).

지키는 것
- 장 열린 뒤 낸 매도가 정지로 미뤄지면 다음 장엔 시가 잣대(개장 전에 이미 있던 주문)
- 두 장의 주문을 한 실행에서 판정하면 장 순서대로 — 거래별 실현손익이 평균 원가 장부와 같다
- 매수·매도 수수료는 설정 그대로 따로(빈 쪽만 기본값)
"""

from __future__ import annotations

from batch.jobs import paper as job
from batch.services import paper as pp
from tests.test_paper_job_1445 import Client, client  # noqa: F401 — 픽스처


def 주문(c: Client, *, aid="A", market="KR", sid=1, side="buy", typ="limit", limit=None, q=1, cur="KRW", decided="2026-10-14",
       session="2026-10-15", basis="open", deferrals=0, tranche=1, ref=None) -> int:  # noqa: ANN001
    cur_ = c.conn.execute(
        "INSERT INTO paper_orders (account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
        " decided_on, fill_session, fill_basis, deferrals, tranche, reason, rule_version, created_at, ref_price)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'x', 1, 't', ?)",
        [aid, market, sid, side, typ, limit, q, cur, decided, session, basis, deferrals, tranche, ref])
    return cur_.lastrowid


def 시세(c: Client, sid: int, day: str, o: float, cl: float, vol: int = 1000, chg: float | None = None, cur: str = "KRW") -> None:  # noqa: E501
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at, change_pct)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 't', 't', ?)", [sid, day, o, max(o, cl), min(o, cl), cl, vol, cur, chg])


def 판정(c: Client, market: str, td: str, frozen: set[int] | None = None) -> tuple:
    flows, _ = job._flows(c)
    fills, _ = job._fills(c)
    기록 = {}
    새 = job.fill_orders(c, market, td, flows, fills, {}, {}, frozen or set(), "now", 기록)
    return 새, 기록


def test_A_장_열린_뒤_매도가_미뤄지면_다음_장_시가(client: Client) -> None:  # noqa: F811
    """장 열린 뒤 낸 매도(fill_basis=close)가 정지로 다음 장에 미뤄지면, 다음 장엔 시가가 주문 뒤인데도 종가로 찬다."""
    c = client
    주문(c, side="buy", limit=10_000, q=4)
    시세(c, 1, "2026-10-15", 10_100, 9_900)
    판정(c, "KR", "2026-10-15")
    s = 주문(c, side="sell", typ="market", q=4, decided="2026-10-15", session="2026-10-16", basis="close", tranche=0)
    시세(c, 1, "2026-10-16", 0, 9_900, vol=0)  # 정지
    판정(c, "KR", "2026-10-16")
    # 저장 잣대는 그대로(KIS 비교의 첫날 줄이 종가 판정 표본 밖에 남게, 25.1621) — 판정만 시가
    assert c.q("SELECT fill_session, deferrals, fill_basis FROM paper_orders WHERE id=?", [s]) == [("2026-10-19", 1, "close")]
    시세(c, 1, "2026-10-19", 8_000, 9_500)
    판정(c, "KR", "2026-10-19")
    assert c.q("SELECT price, fill_date FROM paper_fills WHERE order_id=?", [s]) == [(8000.0, "2026-10-19")]


def test_B_두_장을_한_번에_판정하면_장_순서대로(client: Client) -> None:  # noqa: F811
    """어제 시세가 늦어 열어 둔 10-15 회차와 10-16 매도를 한 실행에서 판정 — 매도가 먼저라 실현손익이 장부와 다르다."""
    c = client
    주문(c, side="buy", limit=10_000, q=4, decided="2026-10-12", session="2026-10-13")
    시세(c, 1, "2026-10-13", 10_000, 9_900)
    판정(c, "KR", "2026-10-13")
    주문(c, side="buy", limit=9_000, q=4, decided="2026-10-14", session="2026-10-15", tranche=2)
    s = 주문(c, side="sell", typ="market", q=4, decided="2026-10-15", session="2026-10-16", tranche=0)
    시세(c, 1, "2026-10-15", 9_100, 8_900)
    시세(c, 1, "2026-10-16", 9_500, 9_600)
    판정(c, "KR", "2026-10-16")
    fills, _ = job._fills(c)
    장부 = {r["order_id"]: r["pnl_krw"] for r in pp.realized([f for f in fills if f.account_id == "A"])}
    assert c.q("SELECT realized_krw FROM paper_fills WHERE order_id = ?", [s]) == [(장부[s],)]  # 예전엔 −2,126 대 −126


def test_C_매수_매도_수수료는_따로() -> None:
    co = job.costs_for("KR", {"kr_buy_pct": 0.015, "kr_sell_pct": 0.25}, {}, "2026-10-15")
    assert (co.buy_fee_pct, co.sell_fee_pct) == (0.015, 0.25)  # 예전엔 둘 다 0.1325
    co2 = job.costs_for("KR", {"kr_sell_pct": 0.25}, {}, "2026-10-15")
    assert (co2.buy_fee_pct, co2.sell_fee_pct) == (0.015, 0.25)  # 빈 쪽만 기본값(예전엔 매수에도 0.25)
    snap = job.cost_snapshot("US", {"us_buy_pct": 0.1}, {}, "2026-10-15")
    assert (snap["buy_fee_pct"], snap["sell_fee_pct"]) == (0.1, 0.015)
