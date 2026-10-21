"""KIS모의투자 미국 감사 [권고] 1·2 (docs/paper.md 15장, docs/infra.md 25.1605).

지키는 것
- 같은 티커가 두 행(거래소를 옮긴 종목)이면 KIS 보유는 상장 중인 행에 붙는다 — 옛(폐지) 행에 붙으면 거짓 어긋남·영영 안 팖
- 정리 대상인데 정리를 못 내는 네 길(주문가능 0·코드 없음·기업행위 경계·멈춤)도 까닭을 남긴다
"""

from __future__ import annotations

from batch.jobs import broker_mirror as job
from batch.services import broker_mirror as bm
from tests.test_kis_vts_us_1485 import c  # noqa: F401 — 픽스처
from tests.test_paper_job_1445 import Client


def test_같은_티커_두_행이면_상장_중인_행(c: Client) -> None:  # noqa: F811
    c.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, sector, currency, status, source,"
                   " fetched_at, asset_type) VALUES (9, 'AAPL', 'NYSE', 'US', '옛행', '전자', 'USD', 'delisted', 't',"
                   " 't', 'stock')")  # fmt: skip
    상장 = c.q("SELECT id FROM stocks WHERE ticker = 'AAPL' AND status = 'active'")[0][0]
    assert job._ids(c, {"AAPL"}, "US") == {"AAPL": 상장}
    # 상장 중인 행이 여럿이면 새 id — 새 행(12)의 시장 이름이 색인에서 앞서도(동률을 색인 순서에 맡기지 않게,
    # 25.1607, 25.1605 반박 [권고])
    c.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, sector, currency, status, source,"
                   " fetched_at, asset_type) VALUES (12, 'AAPL', 'AMEX', 'US', '새행', '전자', 'USD', 'active', 't',"
                   " 't', 'stock')")  # fmt: skip
    assert job._ids(c, {"AAPL"}, "US") == {"AAPL": 12}


def test_정리를_못_내는_길도_까닭을_남긴다() -> None:
    공통 = dict(internal_qty={}, bought_before={7}, already=set(), cleaned_today=set(), currency="USD",
              closes={7: 50.0})  # fmt: skip
    for codes, broker, frozen, 까닭 in (({7: "X"}, {7: (3, 0)}, None, "증권사 주문가능 수량 0 — 정리 못 함"),
                                       ({}, {7: (3, 3)}, None, "종목 코드 없음 — 정리 못 함"),
                                       ({7: "X"}, {7: (3, 3)}, {7}, "기업행위 경계 — 정리 안 함")):  # fmt: skip
        낼것, 안냄 = bm.plan([], codes, broker=broker, frozen=frozen, halted=False, **공통)  # type: ignore[arg-type]
        assert 낼것 == [] and 안냄 == [(None, 7, 까닭)]
    낼것, 안냄 = bm.plan([], {7: "X"}, broker={7: (3, 3)}, halted=True, **공통)  # type: ignore[arg-type]
    assert 낼것 == [] and 안냄 == [(None, 7, "멈춤 스위치가 켜져 있음 — 정리 안 함")]
    # 주문가능이 일부면 그만큼만 내고 나머지 까닭도
    낼것, 안냄 = bm.plan([], {7: "X"}, broker={7: (3, 1)}, halted=False, **공통)  # type: ignore[arg-type]
    assert [(i.side, i.quantity) for i in 낼것] == [("sell", 1)]
    assert 안냄 == [(None, 7, "증권사 주문가능 수량 모자람 — 일부만 정리")]
    # 정리할 수 있으면 예전처럼 낸다
    낼것, 안냄 = bm.plan([], {7: "X"}, broker={7: (3, 3)}, halted=False, **공통)  # type: ignore[arg-type]
    assert [(i.side, i.quantity) for i in 낼것] == [("sell", 3)] and 안냄 == []
