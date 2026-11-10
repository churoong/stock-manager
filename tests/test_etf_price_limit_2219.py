"""국내 ETF 가격제한 끝을 ETF 호가 단위로 잰다 (docs/infra.md 25.2219, 11-09 결함 회차 [참고] ①).

예전엔 `kr_price_limits` 가 ETF 에도 주식 호가 표(2만~5만원 50원)를 써, 기준가 21,005원 ETF 의 하한가(14,705원)를
14,700원으로 봤다 — 실제 하한가에 갇힌 시장가 매도를 찬 것으로 판정할 수 있었다."""

from __future__ import annotations

import pytest

from batch.services import paper as pp
from tests.test_paper_job_1445 import client  # noqa: F401 — 픽스처


def test_ETF_끝은_5원_단위() -> None:
    assert pp.kr_price_limits(21_005, etf=True) == (27_305, 14_705)
    assert pp.kr_price_limits(21_005) == (27_300, 14_700)  # 주식 표는 그대로


def test_ETF_2000원_미만은_1원() -> None:
    assert pp.kr_price_limits(1_501, etf=True) == (1_951, 1_051)


def _주문(side: str) -> pp.Order:
    return pp.Order(1, "X", 3, side, "market", None, 10, "KRW", "2026-10-19")


@pytest.mark.parametrize(("etf", "갇힘"), [(True, True), (False, False)])
def test_ETF_하한가_시장가_매도는_갇힌다(etf: bool, 갇힘: bool) -> None:
    등락 = (14_705 / 21_005 - 1) * 100
    bar = pp.Bar("2026-10-19", 14_705, 14_705, 1_000, 등락, etf=etf)
    assert pp.limit_locked(_주문("sell"), bar, 14_705) is 갇힘


def test_ETF_상한가_시장가_매수도() -> None:
    등락 = (27_305 / 21_005 - 1) * 100
    bar = pp.Bar("2026-10-19", 27_305, 27_305, 1_000, 등락, etf=True)
    assert pp.limit_locked(_주문("buy"), bar, 27_305) is True


@pytest.mark.parametrize(("asset_type", "기대"), [("etf", "open"), ("stock", "filled")])
def test_배치가_ETF_표시를_넘긴다(client, monkeypatch: pytest.MonkeyPatch, asset_type: str, 기대: str) -> None:  # noqa: F811
    """기준가 10,005원, 시가·종가 7,005원 — ETF 하한가(5원 단위)라 시장가 매도는 미룸, 주식 표로는 하한가(7,000) 위라 찬다"""
    from tests.test_etf_missing_bar_2216 import _매도_만들기, _종류
    from tests.test_paper_job_1445 import _시세, _하루

    c = client
    _매도_만들기(c, monkeypatch)
    _종류(c, asset_type)
    _시세(c, "2026-10-19", 10_000, 10_000)
    c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, change_pct, currency, source,"
                   " fetched_at) VALUES (3, '2026-10-19', 7005, 7005, 7005, 7005, 1000, ?, 'KRW', 't', 't')",
                   [(7_005 / 10_005 - 1) * 100])  # fmt: skip
    _하루(c, monkeypatch, "2026-10-19", signals=False)
    상태 = {s for (s,) in c.q("SELECT status FROM paper_orders WHERE side = 'sell'")}
    assert 상태 == {기대}, 상태
