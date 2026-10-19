"""국내 시장가 주문이 가격제한폭 끝이면 찼다고 보지 않는다 (docs/paper.md 2.3, docs/infra.md 25.1553 — 25.1549 감사 남은 것).

지키는 것
- 시가가 상한가인 날 시장가 매수(C·R 시가 일괄)는 안 찬다 — 매수는 취소되고 결정 원장에 "missed"
- 상한가는 기준가(종가 ÷ (1 + 등락률)) × 1.3 을 호가 단위로 내린 값 — 한 단위 아래 시가는 찬다(손계산)
- 하한가로 연 날 시장가 매도는 취소가 아니라 다음 장으로 미룬다
- 등락률이 없으면(대체 출처) 예전처럼 찬다 · 미국·지정가는 그대로
"""

from __future__ import annotations

from typing import Any

import pytest

from batch.services import paper as pp
from batch.services import report_picks
from tests.test_paper_accounts_1458 import _하루, c  # noqa: F401 — 픽스처
from tests.test_paper_job_1445 import Client


def _o(**kw: Any) -> pp.Order:
    기본 = {"id": 1, "account_id": "C", "stock_id": 1, "side": "buy", "order_type": "market", "limit_price": None,
            "quantity": 10, "currency": "KRW", "fill_session": "2026-10-15", "ref_price": 10_000}  # fmt: skip
    return pp.Order(**{**기본, **kw})


def test_시가_상한가면_시장가_매수가_안_찬다() -> None:
    bar = pp.Bar("2026-10-15", 13_000, 13_000, 1_000, 30.0)
    assert pp.judge(_o(), bar) == (None, "시가 상한가 — 시장가 매수가 찼다고 보지 않는다")
    # 장 열린 뒤 낸 주문은 종가로 — 종가가 상한가면 같다
    assert pp.judge(_o(fill_basis="close"), pp.Bar("d", 10_000, 13_000, 1_000, 30.0))[0] is None
    # 시가는 상한가가 아니면 찬다(그날 종가가 상한가여도 시가 체결)
    assert pp.judge(_o(), pp.Bar("d", 12_000, 13_000, 1_000, 30.0)) == (12_000, "시가")


def test_상한가는_호가_단위로_내린_값() -> None:
    # 기준가 12,340 → × 1.3 = 16,042 → 10원 단위로 내려 16,040. 등락률은 소수 둘째 자리(29.98)
    assert pp.judge(_o(), pp.Bar("d", 16_040, 16_040, 1_000, 29.98))[0] is None
    assert pp.judge(_o(), pp.Bar("d", 16_030, 16_040, 1_000, 29.98)) == (16_030, "시가")


def test_하한가면_시장가_매도가_안_찬다() -> None:
    매도 = _o(side="sell", ref_price=None)
    가격, 까닭 = pp.judge(매도, pp.Bar("d", 7_000, 7_000, 1_000, -30.0))
    assert 가격 is None and 까닭.endswith(pp.LOCKED_SELL)
    assert pp.judge(매도, pp.Bar("d", 7_100, 7_000, 1_000, -30.0)) == (7_100, "시가")


def test_정리매매처럼_제한폭_밖_등락률이면_끝이_없다() -> None:
    """가격제한이 없는 날(정리매매·기준가 재설정)은 −65% 여도 하한가가 아니다 (25.1560 — 25.1553 반박 [권고])"""
    매도 = _o(side="sell", ref_price=None)
    assert pp.judge(매도, pp.Bar("d", 4_000, 3_500, 1_000, -65.0)) == (4_000, "시가")


def test_모르면_예전처럼_미국_지정가는_그대로() -> None:
    assert pp.judge(_o(), pp.Bar("d", 13_000, 13_000, 1_000, None)) == (13_000, "시가")
    assert pp.judge(_o(currency="USD"), pp.Bar("d", 13.0, 13.0, 1_000, 30.0)) == (13.0, "시가")
    지정가 = _o(order_type="limit", limit_price=13_000)
    assert pp.judge(지정가, pp.Bar("d", 13_000, 13_000, 1_000, 30.0)) == (13_000, "종가 ≤ 회차 가격")


def _시세(cl: Client, sid: int, day: str, open_: float, close: float, change_pct: float | None) -> None:
    cl.conn.execute(
        "INSERT INTO prices (stock_id, date, open, high, low, close, volume, change_pct, currency, source, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 1000, ?, 'KRW', 't', 't')",
        [sid, day, open_, max(open_, close), min(open_, close), close, change_pct],
    )


def test_배치_상한가_시가_매수는_취소되고_놓친_기회로(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setattr(report_picks, "select_top", lambda rows, n=5: rows)
    _하루(c, monkeypatch, "2026-10-14")
    for sid in (1, 3):
        _시세(c, sid, "2026-10-15", 13_000, 13_000, 30.0)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    상태 = dict(c.q("SELECT account_id, status FROM paper_orders WHERE account_id IN ('C', 'R1', 'R2')"))
    assert 상태 == {"C": "expired", "R1": "expired", "R2": "expired"}
    assert not c.q("SELECT 1 FROM paper_fills WHERE account_id IN ('C', 'R1', 'R2')")
    까닭 = c.q("SELECT close_note FROM paper_orders WHERE account_id = 'C'")[0][0]
    assert "상한가" in 까닭
    assert c.q("SELECT kind FROM paper_decisions WHERE account_id = 'C' AND kind = 'missed'") == [("missed",)]


def test_배치_하한가_시장가_매도는_다음_장으로(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setattr(report_picks, "select_top", lambda rows, n=5: rows)
    _하루(c, monkeypatch, "2026-10-14")
    for sid in (1, 3):
        _시세(c, sid, "2026-10-15", 10_000, 9_000, -10.0)  # C 는 시가 10,000 에 사고, 종가 −10% 로 단기 손절
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    매도 = c.q("SELECT id, fill_session FROM paper_orders WHERE account_id = 'C' AND side = 'sell'")
    assert 매도, "손절 매도 주문이 있어야 시험이 뜻을 가진다"
    for sid in (1, 3):
        _시세(c, sid, "2026-10-16", 6_300, 6_300, -30.0)
    _하루(c, monkeypatch, "2026-10-16", signals=False)
    상태 = c.q("SELECT status, fill_session, deferrals FROM paper_orders WHERE id = ?", [매도[0][0]])[0]
    assert 상태[0] == "open" and 상태[1] > "2026-10-16" and 상태[2] == 1
    assert not c.q("SELECT 1 FROM paper_fills WHERE account_id = 'C' AND side = 'sell'")
