"""미루는 매도는 이 실행의 주문 장 뒤로 (docs/paper.md 2.3, docs/infra.md 25.1803 — 결함 루프 2026-10-26).

이미 지난 장(판정일 이하, 장 끝난 뒤 손 실행이면 오늘 끝난 장)으로 미루면 다음 실행이 미루기를 정할 때 이미 알던 시가로
팔고, KIS 베끼기(그 실행의 장 주문만 냄)는 그 매도를 내지 못했다. 25.1714·25.1715 가 기다림 끝·시장 전체 빠짐·환율 없음을
판정일 다음 장으로 고쳤지만 정지·하한가 길(`날` 기준)과 장 끝난 뒤 손 실행은 남아 있었다.
"""

from __future__ import annotations

import pytest

from tests.test_missing_bar_1713 import _3번_주문, _손실행, _시세3
from tests.test_paper_job_1445 import Client, _시세, _하루, client  # noqa: F401 — 픽스처


def _손절_매도(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """3번을 10-15 에 사고 10-16 종가 −9% 손절 → 10-19 매도 주문"""
    _3번_주문(c, monkeypatch)
    _시세(c, "2026-10-15", 10_000, 10_000)
    _시세3(c, "2026-10-15", 10_100, 9_900)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    _시세(c, "2026-10-16", 10_000, 10_000)
    _시세3(c, "2026-10-16", 9_100, 9_000)
    _하루(c, monkeypatch, "2026-10-16", signals=False)
    assert c.q("SELECT DISTINCT fill_session FROM paper_orders WHERE side = 'sell'") == [("2026-10-19",)]


def test_빠진_행_뒤_날_행이_있는_매도는_오늘_다음_장으로(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """10-19 시장은 받았는데 3번 행이 없고(기다림), 10-20 에 3번 행이 들어왔다(거래 재개). 10-20 판정 실행(10-21 아침)은
    10-19 를 정지로 보고 미루는데 — 예전엔 `next_session(10-19)` = 10-20(이미 지난 장)이라 10-22 실행이 10-20 시가로 팔고
    10-21 KIS 베끼기는 그 매도를 내지 못했다. 이제 10-21"""
    c = client
    _손절_매도(c, monkeypatch)
    _시세(c, "2026-10-19", 10_000, 10_000)
    _하루(c, monkeypatch, "2026-10-19", signals=False)
    assert c.q("SELECT DISTINCT status, fill_session FROM paper_orders WHERE side = 'sell'") == [("open", "2026-10-19")]
    _시세(c, "2026-10-20", 10_000, 10_000)
    _시세3(c, "2026-10-20", 8_000, 8_100)
    _하루(c, monkeypatch, "2026-10-20", signals=False)
    assert c.q("SELECT DISTINCT status, fill_session FROM paper_orders WHERE side = 'sell'") == [("open", "2026-10-21")]


def test_장_끝난_뒤_손_실행이_미루는_정지_매도는_주문_장으로(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """10-19 3번 정지(거래량 0), 10-20 아침 단계는 건너뜀, 10-20 장 끝난 뒤 손 실행(trade_date 10-19). 이 실행이 새로 내는
    주문은 10-21 장(25.1447)인데 미룬 매도는 10-20(이미 끝난 장)으로 갔다 — 다음 실행이 10-20 시가로 팔았다. 이제 10-21"""
    c = client
    _손절_매도(c, monkeypatch)
    _시세(c, "2026-10-19", 10_000, 10_000)
    _시세3(c, "2026-10-19", 9_000, 9_000, volume=0)
    _손실행(c, monkeypatch, "2026-10-19", "2026-10-20", signals=False)
    assert c.q("SELECT DISTINCT status, fill_session FROM paper_orders WHERE side = 'sell'") == [("open", "2026-10-21")]


def test_보통_정지는_예전과_같다(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """판정일 = 매도 장(아침 실행)이면 다음 장 그대로 — 바뀌는 것은 지난 장으로 가던 경우뿐"""
    c = client
    _손절_매도(c, monkeypatch)
    _시세(c, "2026-10-19", 10_000, 10_000)
    _시세3(c, "2026-10-19", 9_000, 9_000, volume=0)
    _하루(c, monkeypatch, "2026-10-19", signals=False)
    assert c.q("SELECT DISTINCT status, fill_session FROM paper_orders WHERE side = 'sell'") == [("open", "2026-10-20")]
