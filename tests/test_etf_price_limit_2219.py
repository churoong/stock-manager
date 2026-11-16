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


def test_되살린_기준가_오차가_반_호가를_넘는_ETF_가격대도_끝을_놓치지_않는다() -> None:
    """기준가 2,000~200,000원 ETF 를 5원 간격으로 전수 — 등락률(소수 둘째 자리)로 되살려도 거래소 상·하한가에서
    시장가 매수·매도가 갇힌다(25.2233, 11-16 결함 회차 — 고치기 전 2,250건 놓침)"""
    놓침 = 0
    for 기준 in range(2_000, 200_001, 5):
        위, 아래 = pp.kr_price_limits(기준, etf=True)
        for 가격, side in ((위, "buy"), (아래, "sell")):
            등락 = round((가격 / 기준 - 1) * 100, 2)
            bar = pp.Bar("2026-10-19", 가격, 가격, 1_000, 등락, etf=True)
            if not pp.limit_locked(_주문(side), bar, 가격):
                놓침 += 1
    assert 놓침 == 0


def test_재현_42525원_ETF_하한가_매도() -> None:
    bar = pp.Bar("2026-10-19", 29_770, 29_770, 1_000, -29.99, etf=True)
    assert pp.kr_price_limits(42_525, etf=True)[1] == 29_770
    assert pp.limit_locked(_주문("sell"), bar, 29_770) is True
    assert pp.limit_locked(_주문("sell"), bar, 29_775 + 50) is False  # 끝에서 떨어진 값은 찬다


def test_앞_종가가_있으면_끝_한_호가_안쪽은_찬다() -> None:
    """앞 거래일 종가(기준가)가 되살린 값과 맞으면 그것으로 — 등락률만으로는 이웃 호가 기준가와 가를 수 없어 후보로
    보면 안쪽 값도 갇혔다(전수 79,202 가운데 24,401). 앞 종가가 있으면 놓침 0·안쪽 갇힘 0(25.2233)"""
    놓침 = 안쪽 = 0
    for 기준 in range(2_000, 200_001, 5):
        위, 아래 = pp.kr_price_limits(기준, etf=True)
        for 가격, side, 끝 in ((위, "buy", True), (아래, "sell", True), (위 - 5, "buy", False), (아래 + 5, "sell", False)):
            bar = pp.Bar("d", 가격, 가격, 1, round((가격 / 기준 - 1) * 100, 2), etf=True, prev_close=기준)
            갇힘 = pp.limit_locked(_주문(side), bar, 가격)
            놓침 += 끝 and not 갇힘
            안쪽 += (not 끝) and 갇힘
    assert (놓침, 안쪽) == (0, 0)


def test_앞_종가가_되살린_기준가와_멀면_후보로() -> None:
    """분배락·기준가 재설정 등으로 앞 종가가 기준가가 아니면 쓰지 않는다 — 후보(보수 쪽)로 끝을 놓치지 않는다"""
    bar = pp.Bar("2026-10-19", 29_770, 29_770, 1_000, -29.99, etf=True, prev_close=40_000)
    assert pp.limit_locked(_주문("sell"), bar, 29_770) is True


def test_배치가_ETF_앞_종가를_넘긴다(client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """기준가 40,020원 ETF 의 하한가는 28,015원 — 시가 28,020원(한 호가 안쪽, 등락률 −29.99)은 찬다. 앞 종가를 넘기지
    않으면 등락률만으로는 이웃 기준가(40,025)와 갈리지 않아 갇힌 것으로 봤다(25.2233)"""
    from tests.test_etf_missing_bar_2216 import _매도_만들기, _종류
    from tests.test_paper_job_1445 import _시세, _하루

    c = client
    _매도_만들기(c, monkeypatch)
    _종류(c, "etf")
    c.conn.execute("UPDATE prices SET close = 40020 WHERE stock_id = 3 AND date = '2026-10-16'")
    _시세(c, "2026-10-19", 10_000, 10_000)
    c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, change_pct, currency, source,"
                   " fetched_at) VALUES (3, '2026-10-19', 28020, 28020, 28020, 28020, 1000, -29.99, 'KRW', 't', 't')")
    _하루(c, monkeypatch, "2026-10-19", signals=False)
    assert {s for (s,) in c.q("SELECT status FROM paper_orders WHERE side = 'sell'")} == {"filled"}
