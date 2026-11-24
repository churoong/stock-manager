"""절대 한도·가격 깃 그림자 계측 (docs/paper.md 15.12, docs/infra.md 25.2284).

낸 KIS모의투자 주문이 가격 깃·거래대금 깃에 걸렸을 건수를 사후에 센다 — 막지 않고, 쓰지 않고, 주문 경로에 닿지 않는다.
"""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from batch.core import markets
from batch.jobs import order_limits as jo
from batch.services import order_limits as ol
from batch.services import scoreboard

뿌리 = Path(__file__).resolve().parent.parent


def _o(i: int, **kw) -> dict:
    return {"id": i, "broker": "kis_vts", "market": "KR", "stock_id": 1, "side": "buy", "order_type": "limit", "limit_price": 10_000.0,
            "quantity": 10, "trade_date": "2026-11-20", "reason": "베낌", "status": "filled", **kw}  # fmt: skip


def _봉(n: int = 20, close: float = 10_000.0, value: float | None = 1_000_000_000.0, volume: int | None = 100_000,
       끝: int = 19) -> list[dict]:  # fmt: skip
    """2026-11-(끝-n+1)..끝 의 일봉 n개"""
    return [{"date": f"2026-11-{끝 - n + 1 + k:02d}", "close": close, "value": value, "volume": volume} for k in range(n)]


def test_상수는_정의처에서_온다() -> None:
    assert ol.ADV_SHARE_MAX == scoreboard.STAGE01_MAX_CAPACITY == 0.01
    assert (ol.WINDOW_DAYS, ol.ADV_DAYS, ol.ADV_MIN_DAYS, ol.PRICE_LOOKBACK_DAYS, ol.STALE_BAR_DAYS) == (28, 20, 10, 45, 14)
    assert markets.KR_DAILY_LIMIT == 0.30


def test_앞_시세는_그날_앞_봉만_본다() -> None:
    봉 = _봉(20) + [{"date": "2026-11-20", "close": 99_999.0, "value": 9e12, "volume": 1}]
    assert ol._앞_시세(봉, "2026-11-20") == (10_000.0, 1_000_000_000.0)  # 그날 봉은 미래 정보 — 쓰지 않는다
    assert ol._앞_시세(봉, "2026-10-31") == (None, None)
    # 거래대금 칸이 비면(미국) 종가 × 거래량, 표본이 하한보다 적으면 평균은 모름
    assert ol._앞_시세(_봉(20, close=50.0, value=None, volume=2_000), "2026-11-20") == (50.0, 100_000.0)
    assert ol._앞_시세(_봉(ol.ADV_MIN_DAYS - 1), "2026-11-20") == (10_000.0, None)
    assert ol._앞_시세(_봉(ol.ADV_MIN_DAYS), "2026-11-20")[1] == 1_000_000_000.0
    # 평균은 마지막 20 거래일만
    긴 = _봉(5, value=9e15, 끝=5) + _봉(20, 끝=25)
    assert ol._앞_시세(긴, "2026-11-26")[1] == 1_000_000_000.0
    # 봉의 차례에 기대지 않는다(25.2285) — 뒤집어 줘도 앞 종가는 가장 늦은 봉, 평균은 마지막 20일
    섞음 = [{**b, "close": 10_000.0 + k} for k, b in enumerate(긴)]
    assert ol._앞_시세(섞음[::-1], "2026-11-26") == (10_024.0, 1_000_000_000.0)
    # 묵은 봉은 "앞 거래일 종가" 가 아니다 — 시세 구멍이면 모름(확정 판정을 내지 않는다)
    assert ol._앞_시세(_봉(20), f"2026-12-{19 + ol.STALE_BAR_DAYS - 30:02d}") == (10_000.0, 1_000_000_000.0)  # 딱 14일
    assert ol._앞_시세(_봉(20), f"2026-12-{20 + ol.STALE_BAR_DAYS - 30:02d}") == (None, None)
    # 거래대금 0·종가 없는 봉은 평균·앞 종가에 쓰지 않는다
    assert ol._앞_시세(_봉(10) + [{"date": "2026-11-19x", "close": None, "value": 5.0, "volume": 1}], "2026-11-20")[0] == 10_000.0
    assert ol._앞_시세(_봉(9, 끝=18) + _봉(1, value=0, volume=0), "2026-11-20")[1] is None  # 0 을 빼면 9일 — 하한 미달


def test_가격_깃은_국내_지정가만_앞_종가_30퍼센트_밖() -> None:
    c = ol.classify
    assert c(_o(1, limit_price=13_000.0), 10_000.0, None)["band"] is False  # 딱 상한
    assert c(_o(1, limit_price=13_001.0), 10_000.0, None)["band"] is True
    assert c(_o(1, limit_price=7_000.0), 10_000.0, None)["band"] is False
    assert c(_o(1, limit_price=6_999.0), 10_000.0, None)["band"] is True
    assert c(_o(1), None, None)["band"] is None  # 앞 종가 모름 — "안 걸림" 이 아니다
    assert c(_o(1, order_type="market", limit_price=None), 10_000.0, None)["band"] is None
    assert c(_o(1, market="US", limit_price=999.0), 10.0, None)["band"] is None  # 미국은 가격제한폭이 없다


def test_금액과_거래대금_몫() -> None:
    c = ol.classify(_o(1, limit_price=10_000.0, quantity=1_000), 9_000.0, 1_000_000_000.0)
    assert c["notional"] == 10_000_000.0 and c["adv_share"] == pytest.approx(0.01) and c["adv_breach"] is False  # 딱 1%
    assert ol.classify(_o(1, limit_price=10_000.0, quantity=1_001), 9_000.0, 1_000_000_000.0)["adv_breach"] is True
    # 시장가는 앞 종가로 금액을 낸다. 앞 종가도 없으면 모름
    시장가 = _o(1, order_type="market", limit_price=None, quantity=100)
    assert ol.classify(시장가, 9_000.0, 1e9)["notional"] == 900_000.0
    assert ol.classify(시장가, None, 1e9) == {"notional": None, "band": None, "adv_share": None, "adv_breach": None}
    assert ol.classify(_o(1), 10_000.0, None)["adv_breach"] is None


def test_요약은_매수만_걸림으로_세고_매도는_참고다() -> None:
    bars = {1: _봉(20), 2: _봉(20, value=1_000_000.0), 3: []}
    orders = [
        _o(1),  # 10만 원 — 걸리지 않음
        _o(2, limit_price=14_000.0),  # 가격 깃
        _o(3, stock_id=2, quantity=10),  # 10만 원 > 100만 × 1% — 거래대금 깃
        _o(4, stock_id=2, side="sell", quantity=10),  # 매도 — 막지 않는다(참고 수)
        _o(5, stock_id=3),  # 시세 없음 — 모름
        _o(6, trade_date="2026-11-21", quantity=50),
        _o(7, market="US", stock_id=9, limit_price=20.0, quantity=5),
        _o(8, side="sell", limit_price=6_000.0, quantity=1),  # 가격제한폭 밖 지정가 **매도** — 가격 깃은 양쪽 모두
        _o(9, side="sell", order_type="market", limit_price=None, quantity=1),  # 국내 시장가 — 가격 깃 대상이 아니다
        _o(10, stock_id=3, order_type="market", limit_price=None, status="rejected"),  # 거부된 것도 보내려던 주문
    ]
    s = ol.summarize(orders, bars)
    kr = s["KR"]
    assert (kr["orders"], kr["buys"], kr["sells"], kr["days"]) == (9, 6, 3, 2)
    assert (kr["band_breach"], kr["band_unknown"]) == (2, 1)  # 시장가 둘은 "앞 종가 모름" 으로 세지 않는다
    assert (kr["adv_breach"], kr["adv_unknown"], kr["sell_adv_over"]) == (1, 2, 1)
    assert kr["adv_breach_stock_day"] == 1
    # 금액: 100,000 · 140,000 · 100,000 · (모름 5번도 지정가라 금액은 난다) 100,000 · 500,000
    assert kr["max_notional"] == 500_000.0 and kr["p95_notional"] == 500_000.0
    assert kr["max_daily_buy"] == 500_000.0  # 11-21 하루 500,000 > 11-20 합 440,000
    us = s["US"]
    assert (us["orders"], us["band_breach"], us["band_unknown"], us["adv_unknown"]) == (1, 0, 0, 1)
    assert us["max_notional"] == 100.0


def test_같은_종목_같은_날_회차의_합으로도_센다() -> None:
    """25.2285 (25.2284 교차검증 [권고]) — 분할 매수 회차가 줄마다 1% 밑이면 합이 넘어도 0건이었다. 주문이 통째로
    커지는 사고에서는 회차가 같이 커진다"""
    bars = {1: _봉(20)}  # 평균 거래대금 10억 → 1% = 1,000만 원
    orders = [_o(k, limit_price=9_800.0, quantity=500) for k in (1, 2, 3)]  # 각 490만(0.49%), 합 1,470만(1.47%)
    s = ol.summarize(orders + [_o(4, trade_date="2026-11-21", quantity=500)], bars)["KR"]
    assert s["adv_breach"] == 0 and s["adv_breach_stock_day"] == 1  # 11-20 하루만 — 11-21 은 한 줄(0.5%)
    # 계좌를 가르지 않는다(시장에 주는 충격은 합) — 첫째·도전자가 같은 종목을 같은 날 사면 더한다
    둘 = [_o(1, quantity=600), _o(2, quantity=600, broker="kis_vts2")]
    assert ol.summarize(둘, bars)["KR"]["adv_breach_stock_day"] == 1


def test_하루_매수_합은_계좌마다_낸_것의_최대다() -> None:
    """25.2285 — 첫째·도전자 두 계좌의 매수를 더하면 "하루 총액 상한" 의 재료가 부풀려진다(덜 막는 쪽으로 틀린다)"""
    orders = [_o(1, quantity=30), _o(2, quantity=20), _o(3, quantity=40, broker="kis_vts2")]
    s = ol.summarize(orders, {1: _봉(20)})["KR"]
    assert s["max_daily_buy"] == 500_000.0  # 첫째 30 + 20 주 — 도전자 40 주를 더한 900,000 이 아니다
    assert s["max_notional"] == 400_000.0


def test_95_분위는_최근접_순위다() -> None:
    assert ol._분위([float(k) for k in range(1, 21)], 0.95) == 19.0  # 20건이면 19번째 — 최댓값이 아니다
    assert ol._분위([float(k) for k in range(1, 101)], 0.95) == 95.0
    assert ol._분위([7.0], 0.95) == 7.0 and ol._분위([], 0.95) is None


def test_줄은_건수와_금액을_적고_한도는_정하지_않는다() -> None:
    줄 = ol.lines(ol.summarize([_o(1), _o(2, market="US", stock_id=9, limit_price=20.0)], {1: _봉(20)}), "2026-10-27", "2026-11-24")
    글 = "\n".join(줄)
    assert "docs/paper.md 15.12" in 줄[0] and "막지 않고 세기만" in 줄[0]
    assert "가격제한폭 ±30% 밖): 걸렸을 0건" in 글 and "20일 평균 거래대금의 1%): 걸렸을 0건" in 글
    assert "최대 100,000원" in 글 and "최대 $200.00" in 글 and "사용자 결정(한도 미정)" in 글
    assert 글.count("가격 깃(지정가가") == 1  # 미국 줄에는 가격 깃이 없다
    assert ol.lines({}, "a", "b")[-1] == "  주문 없음"


class _C:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE paper_broker_orders (id INTEGER PRIMARY KEY, broker TEXT, market TEXT, stock_id INTEGER, side TEXT,"
                          " order_type TEXT, limit_price REAL, quantity INTEGER, trade_date TEXT, reason TEXT, status TEXT)")  # fmt: skip
        self.conn.execute("CREATE TABLE prices (stock_id INTEGER, date TEXT, close REAL, volume INTEGER, value INTEGER)")
        self.본: list[tuple[str, list]] = []

    def execute(self, sql: str, params: list | None = None):
        self.본.append((sql, list(params or [])))
        if not sql.lstrip().upper().startswith("SELECT"):
            raise AssertionError(f"읽기 전용이어야 한다: {sql[:40]}")
        cur = self.conn.execute(sql, params or [])
        이름 = [d[0] for d in cur.description or []]
        행 = cur.fetchall()
        return type("R", (), {"rows": 행, "dicts": lambda self: [dict(zip(이름, r, strict=True)) for r in 행]})()


def test_읽기는_창_안_주문과_그_종목의_봉만이고_쓰지_않는다() -> None:
    c = _C()
    c.conn.executemany("INSERT INTO paper_broker_orders (broker, market, stock_id, side, order_type, limit_price, quantity,"
                       " trade_date, reason, status) VALUES ('kis_vts', 'KR', ?, 'buy', 'limit', ?, 10, ?, '베낌', ?)",
                       [(1, 14_000.0, "2026-11-20", "filled"), (1, 10_000.0, "2026-10-26", "filled"),
                        (2, 10_000.0, "2026-10-27", "filled"), (2, 12_900.0, "2026-11-20", "rejected")])  # fmt: skip
    for sid in (1, 2, 3):
        # 마지막 봉(11-19)만 종가가 다르다 — 앞 종가가 가장 늦은 봉에서 오는지(읽는 차례)
        c.conn.executemany("INSERT INTO prices VALUES (?, ?, ?, 100000, 1000000000)",
                           [(sid, b["date"], 10_000 if b["date"] < "2026-11-19" else 9_900) for b in _봉(20)]
                           + [(sid, "2026-09-11", 10_000), (sid, "2026-09-12", 10_000)])  # fmt: skip
    줄 = jo.run(c, "2026-11-24")
    assert "2026-10-27~2026-11-24" in 줄[0]  # 오늘 − 28일부터 — 10-26 주문은 밖, 10-27 은 안
    # 거부된 주문도 센다(상태와 무관). 앞 종가 9,900 × 1.3 = 12,870 — 12,900 도 가격 깃(10,000 기준이면 안 걸린다)
    assert "KR: 주문 3건(매수 3·매도 0)" in 줄[1] and "걸렸을 2건" in 줄[2]
    (s1, p1), (s2, p2) = c.본
    assert s1 == jo.ORDERS_SQL and p1 == ["2026-10-27"]
    assert s2 == jo.PRICES_SQL and p2 == ["[1, 2]", "2026-09-12", "2026-11-24"]  # 창 앞 45일, 주문 없는 종목 3 은 안 읽는다
    # 주문이 없으면 시세를 읽지 않는다
    빈 = _C()
    assert jo.run(빈, "2026-11-24")[-1] == "  주문 없음" and len(빈.본) == 1


def test_주문_경로와_장부에_닿지_않는다() -> None:
    글 = inspect.getsource(ol) + inspect.getsource(jo) + (뿌리 / "scripts" / "order_limits.py").read_text()
    for 금지 in ("INSERT", "UPDATE", "DELETE", "kis_vts", "order_kr", "order_us", "broker_mirror", "trades", "sell_flags"):
        assert 금지 not in 글, 금지
    # 주문을 내는 쪽이 이 모듈을 부르지 않는다 — 계측이 주문을 막거나 늦추는 길이 없다
    for 파일 in ("batch/jobs/broker_mirror.py", "batch/services/broker_mirror.py", "batch/jobs/paper.py", "batch/jobs/intraday_guard.py"):
        assert "order_limits" not in (뿌리 / 파일).read_text(), 파일


def test_워크플로는_수동_읽기_전용이고_출력을_비공개로_낸다() -> None:
    글 = (뿌리 / ".github" / "workflows" / "order-limits.yml").read_text()
    assert "workflow_dispatch:" in 글 and "schedule:" not in 글 and "repository_dispatch" not in 글
    assert "python scripts/order_limits.py 2>&1 | python scripts/ops_tee.py order-limits.txt" in 글
    assert "scripts/publish_output.py" in 글 and "contents: read" in 글


def test_문서가_깃과_읽는_법을_적는다() -> None:
    글 = (뿌리 / "docs" / "paper.md").read_text()
    for 조각 in ("### 15.12 절대 한도·가격 깃 — 그림자 계측", "25.2284", f"최근 {ol.WINDOW_DAYS}일(`WINDOW_DAYS`)",
               f"앞 {ol.ADV_DAYS} 거래일(`ADV_DAYS`) 평균 거래대금의 1%(`ADV_SHARE_MAX`", f"{ol.ADV_MIN_DAYS}일(`ADV_MIN_DAYS`)",
               "매도는 막지 않는다", "사용자 결정", "주문 경로에 닿지 않는다", "상태와 무관하게",
               "가격 깃은 매수·매도 모두 센다", f"(1 ± {markets.KR_DAILY_LIMIT:.0%})", f"그 앞 {ol.PRICE_LOOKBACK_DAYS}일까지",
               f"{ol.STALE_BAR_DAYS}일(`STALE_BAR_DAYS`)", "같은 종목·같은 날", "계좌마다"):  # fmt: skip
        assert 조각 in 글, 조각
