"""KIS만 참인 국내 시장가가 그날 가격제한 끝이었으면 까닭을 따로 센다 (25.2127, 11-03 결함 회차 [참고] ③).

가상은 하한가에 갇힌 시장가 매도·상한가 시장가 매수를 안 찼다고 본다(`paper.limit_locked`, 25.1553·2114). KIS 모의가 그날
찬 줄은 예전엔 "일봉으로 설명 안 됨"(`unexplained`) 으로 갔고, 괴리 알림은 "KIS만 참이 더 많음 — 가상 판정이 비관" 이라
말했다 — 모의 체결이 호가 대기열을 무시한 낙관일 수 있어 그 몫은 방향 판단에서 뺀다.
"""

from __future__ import annotations

from typing import Any

import pytest

from batch.services import broker_mirror as bm


def _줄(**kw: Any) -> dict[str, Any]:
    기본 = {"side": "sell", "i_order_type": "market", "i_limit": None, "b_limit": None, "b_market": "KR",
            "p_open": 1_405, "p_close": 1_405, "p_low": 1_405, "p_high": 1_405, "p_change": -29.93, "i_fill_basis": None}
    return 기본 | kw


@pytest.mark.parametrize(
    ("고침", "까닭"),
    [
        ({}, "limit_locked"),  # 기준가 2,005 → 하한가 1,405 시가 매도
        ({"p_open": 1_406}, "unexplained"),  # 한 단위 위 — 끝이 아니다
        ({"i_fill_basis": "close", "p_open": 1_500}, "limit_locked"),  # 장 열린 뒤 주문은 종가로
        ({"side": "buy", "p_open": 2_605, "p_close": 2_605, "p_change": 29.93}, "limit_locked"),  # 상한가 시가 매수
        ({"b_market": "US"}, "unexplained"),  # 미국은 가격제한이 없다
        ({"p_change": None}, "unexplained"),  # 등락률 없음(대체 출처) — 모름
        ({"p_change": -65.0, "p_open": 700, "p_close": 700}, "unexplained"),  # 정리매매 — 제한폭 밖
        ({"p_open": None}, "limit_locked"),  # 시가 없음 — 가상처럼 종가로 판정(25.2128)
        ({"p_open": None, "p_close": 1_406, "p_change": -29.88}, "unexplained"),  # 종가가 끝 안쪽
        ({"p_open": 0}, "unexplained"),  # 시가 0 — 가상은 정지로 본다(25.2128)
    ],
)
def test_KIS만_참_국내_시장가의_가격제한_끝(고침: dict[str, Any], 까닭: str) -> None:
    assert bm.mismatch_cause(_줄(**고침), False) == 까닭


def test_가상만_참은_이_까닭에_들지_않는다() -> None:
    assert bm.mismatch_cause(_줄(), True) == "market_unfilled"


def test_괴리_알림_방향에서_가격제한_끝은_뺀다() -> None:
    now = {"match_pct": 60.0, "internal_only": 2, "kis_only": 3, "mismatch_causes": {"limit_locked": 2}}
    글 = bm.diverge(now, ["match"], "KR")
    assert 글 is not None and "가상만 참 2 · KIS만 참 3(가격제한 끝 2건 빼면 1): 가상만 참이 더 많음" in 글, 글
    모두끝 = bm.diverge(now | {"internal_only": 0, "kis_only": 4, "mismatch_causes": {"limit_locked": 4}}, ["match"], "KR")
    assert 모두끝 is not None and "KIS만 참 4(가격제한 끝 4건 빼면 0): 가상만 참과 KIS만 참이 같음" in 모두끝, 모두끝
    없음 = bm.diverge(now | {"mismatch_causes": {}}, ["match"], "KR")
    assert 없음 is not None and "KIS만 참이 더 많음" in 없음


def test_가상_limit_locked_와_격자로_같다() -> None:
    """판정 가격·끝 잣대가 가상 `paper.limit_locked`(+ `judge` 의 가격 고르기)와 같다 — 기준가·등락률·시가 격자(25.2128,
    25.2127 교차검증 [참고]: 시가 없음·시가 0 에서 갈렸다)"""
    from batch.services import paper as pp

    갈림 = []
    for 기준 in (1_003, 2_005, 9_990, 49_950, 199_500, 512_000):
        상, 하 = pp.kr_price_limits(기준)
        for 종가 in (하, 하 + pp._tick(하), 상 - pp._tick(상), 상, 기준):
            등락 = round((종가 / 기준 - 1) * 100, 2)
            for 시가 in (None, 0, 하, 상, 기준):
                for 쪽 in ("buy", "sell"):
                    for 바탕 in ("open", "close"):
                        bar = pp.Bar("2026-10-16", 시가, 종가, 1000, 등락)
                        if pp.halted(bar):
                            가상 = False
                        else:
                            가격 = 종가 if 바탕 == "close" or 시가 is None else 시가
                            o = pp.Order(1, "B", 1, 쪽, "market", None, 1, "KRW", "2026-10-16", fill_basis=바탕)
                            가상 = pp.limit_locked(o, bar, 가격)
                        줄 = _줄(side=쪽, p_open=시가, p_close=종가, p_change=등락, i_fill_basis=바탕)
                        if bm._가격제한_끝(줄) != 가상:
                            갈림.append((기준, 종가, 시가, 쪽, 바탕, 가상))
    assert not 갈림, 갈림[:5]


def test_비교_질의가_가격제한_재료를_싣는다() -> None:
    """COMPARE_SQL 이 `p_open`·`p_change`·`b_market` 을 싣지 않으면 운영에선 늘 아니다로 빠져 예전 "설명 안 됨"·
    "가상 판정이 비관" 이 조용히 돌아온다 — 실제 질의로 compare 를 지나 센다(25.2128, 25.2127 교차검증 [권고])"""
    from batch.jobs import broker_mirror as jbm
    from tests.test_paper_fill_1620 import 시세, 주문, 판정
    from tests.test_paper_job_1445 import Client

    c = Client()
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-13', 1000000, 'deposit', 't', 'k1')")  # fmt: skip
    m = 주문(c, aid="B", side="buy", typ="market", q=10, decided="2026-10-15", session="2026-10-16", ref=2_005)
    시세(c, 1, "2026-10-16", 2_605, 2_605, chg=29.93)  # 상한가 시가 — 가상은 시장가 매수가 찼다고 보지 않는다
    판정(c, "KR", "2026-10-16")
    assert c.q("SELECT status FROM paper_orders WHERE id = ?", [m]) == [("expired",)]
    c.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code,"
                   " side, order_type, quantity, trade_date, reason, status, source, created_at, filled_qty,"
                   " avg_price) VALUES ('kis_vts', ?, 'B', 'KR', 1, '005930', 'buy', 'market', 10, '2026-10-16', '베낌',"
                   " 'filled', 'kis_vts', 't', 10, 2605)", [m])  # fmt: skip
    비교 = bm.compare(c.execute(jbm.COMPARE_SQL, ["kis_vts", "2026-10-01", "2026-10-31", "B"]).dicts())
    assert 비교["kis_only"] == 1 and 비교["mismatch_causes"] == {"limit_locked": 1}, 비교
