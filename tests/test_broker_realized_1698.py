"""KIS모의투자 실현손익 — 티커로 묶고, 기록이 KIS 와 어긋난 보유 구간은 모름 (docs/paper.md 15장, docs/infra.md 25.1698).

25.1694 교차검증 [권고] 1·3·4: 같은 티커 두 행·`unknown` 으로 닫은 매수가 실제로 참·산 날 판 것에서 KIS 평균 원가와
다른 값이 조용히 적혔다.
"""

from __future__ import annotations

from batch.jobs import broker_mirror as job
from batch.services import broker_mirror as bm
from tests.test_broker_realized_1694 import _Client, _o
from tests.test_kis_vts_us_1485 import c as c  # noqa: F401 — 픽스처


def test_같은_티커_두_행은_하나의_평균_원가() -> None:
    """옛 행 5주 @100 + 새 행 3주 @120 (같은 티커) → 평균 107.5. 옛 행 3주 @130 → +67.5, 정리 2주 @125 → +35.
    행마다 묶으면 90·10 이었다(교차검증 재현)"""
    r = bm.realized([_o(1, "2026-10-15", "buy", 5, 100.0, sid=1, code="X"), _o(2, "2026-10-16", "buy", 3, 120.0, sid=2, code="X"),
                     _o(3, "2026-10-19", "sell", 3, 130.0, sid=1, code="X"), _o(4, "2026-10-20", "sell", 2, 125.0, sid=1, code="X")],
                    lambda d: None)  # fmt: skip
    assert r == {3: (68.0, 0.0), 4: (35.0, 0.0)}  # 3 × 130 − 3 × 107.5 = 67.5 → 반올림 68(짝수 쪽)


def test_다른_티커는_따로() -> None:
    r = bm.realized([_o(1, "2026-10-15", "buy", 1, 100.0, sid=1), _o(2, "2026-10-15", "buy", 1, 300.0, sid=2),
                     _o(3, "2026-10-16", "sell", 1, 150.0, sid=1)], lambda d: None)  # fmt: skip
    assert r == {3: (50.0, 0.0)}


def test_산_날_판_것은_매수를_먼저() -> None:
    """같은 날 2주 사서 2주 팔고 다음 날 1주 사서 판다 — 매도 먼저면 첫 매도가 모름이고 유령 2주가 남아
    다음 매도 원가가 섞였다"""
    r = bm.realized([_o(1, "2026-10-15", "buy", 2, 100.0), _o(2, "2026-10-15", "sell", 2, 110.0),
                     _o(3, "2026-10-16", "buy", 1, 200.0), _o(4, "2026-10-17", "sell", 1, 250.0)], lambda d: None)  # fmt: skip
    assert r == {2: (20.0, 0.0), 4: (50.0, 0.0)}
    # 보유가 있으면 같은 날도 매도 먼저(가상모의투자와 같은 순서) — 앞서 든 10주 @100 에서 판다
    r2 = bm.realized([_o(1, "2026-10-14", "buy", 10, 100.0), _o(2, "2026-10-15", "buy", 5, 200.0),
                      _o(3, "2026-10-15", "sell", 5, 150.0)], lambda d: None)  # fmt: skip
    assert r2 == {3: (250.0, 0.0)}


def test_기록보다_많이_판_매도가_나오면_그_구간_전부_모름() -> None:
    """`unknown` 으로 닫은 매수(기록 0)가 실제로 찼다 — 10주 @100(기록) + 5주(기록 없음). 8주 매도는 기록 원가로는 +80
    이지만 KIS 평균 원가와 다르다. 정리 7주가 기록보다 많이 팔려 드러나면 같은 구간의 8주 매도도 모름"""
    r = bm.realized([_o(1, "2026-10-14", "buy", 10, 100.0), _o(2, "2026-10-15", "sell", 8, 110.0),
                     _o(3, "2026-10-16", "sell", 7, 105.0),
                     _o(4, "2026-10-20", "buy", 1, 50.0), _o(5, "2026-10-21", "sell", 1, 60.0)], lambda d: None)  # fmt: skip
    assert r[2] == (None, None) and r[3] == (None, None)
    assert r[5] == (10.0, 0.0)  # 다 판 뒤 새로 산 구간은 다시 센다


def test_KIS_잔고와_기록_끝_수량이_다르면_마지막_구간은_모름() -> None:
    rows = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(2, "2026-10-15", "sell", 5, 110.0),
            _o(3, "2026-10-16", "buy", 4, 10.0, sid=2), _o(4, "2026-10-17", "sell", 4, 12.0, sid=2)]  # fmt: skip
    assert bm.realized(rows, lambda d: None) == {2: (50.0, 0.0), 4: (8.0, 0.0)}  # 잔고를 모르면 예전대로
    assert bm.realized(rows, lambda d: None, {"C1": 5}) == {2: (50.0, 0.0), 4: (8.0, 0.0)}  # 맞음
    # KIS 는 7주 — 기록에 없는 2주가 있다. 다 판 C2 를 KIS 도 0 으로 들면 그대로
    assert bm.realized(rows, lambda d: None, {"C1": 7}) == {2: (None, None), 4: (8.0, 0.0)}
    # 다 판 종목을 KIS 가 아직 들고 있으면 그 마지막 구간도 모름
    assert bm.realized(rows, lambda d: None, {"C1": 5, "C2": 3})[4] == (None, None)


def test_환율은_한_번에_읽는다() -> None:
    orders = [_o(i, f"2026-10-{d}", s, 1, 100.0, market="US")
              for i, (d, s) in enumerate([("15", "buy"), ("16", "sell"), ("19", "buy"), ("20", "sell")], 1)]  # fmt: skip
    가짜 = _Client(orders, fx=[("2026-10-08", 1_000.0), ("2026-10-15", 1_300.0), ("2026-10-16", 1_310.0),
                               ("2026-10-19", 1_320.0), ("2026-10-20", 1_330.0)])  # fmt: skip
    assert job.update_realized(가짜, "kis_vts_us", "US") == 2
    assert 가짜.fx_calls == [["2026-10-08", "2026-10-20"]]  # 첫날 7일 앞 ~ 마지막 날
    값 = {x[1][2]: x[1][:2] for x in 가짜.batches[0]}
    assert 값 == {2: [1_000.0, 1_000.0], 4: [1_000.0, 1_000.0]}  # 주가 0 · 환차 = 100 × (1,310 − 1,300)


def test_잔고를_받아_넘긴다() -> None:
    orders = [_o(1, "2026-10-15", "buy", 10, 10_000), _o(2, "2026-10-16", "sell", 4, 11_000)]
    assert job.update_realized(_Client(orders), "kis_vts", "KR", {"C1": 6}) == 1
    assert job.update_realized(_Client(orders), "kis_vts", "KR", {"C1": 9}) == 0  # 모름 = 지금 값(비어 있음)


def test_실행이_잔고를_넘긴다(c) -> None:  # noqa: ANN001, F811
    """`run` 이 같은 실행에서 읽은 KIS 잔고로 견준다 — 기록 끝 2주인데 KIS 가 3주면 모름(안 적음), 2주면 적는다"""
    import pytest

    from batch.sources import kis_vts as kv
    from tests.test_kis_ids_1608 import _두_행
    from tests.test_kis_vts_us_1485 import _kst, _UsSess

    _두_행(c, 30, "NYSE", 2, "NASDAQ")
    x = c.conn.execute
    x("UPDATE paper_broker_orders SET avg_price = 200 WHERE side = 'buy'")  # 픽스처 매수는 평균가 없음(원가 모름)
    for d, v in (("2026-10-14", 1_400.0), ("2026-10-15", 1_410.0)):
        x("INSERT OR REPLACE INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', ?, ?, 't', 't')", [d, v])
    x("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side, order_type,"
      " limit_price, quantity, trade_date, reason, status, filled_qty, avg_price, source, created_at) VALUES"
      " ('kis_vts_us', NULL, 'B', 'US', 30, 'AAPL', 'sell', 'limit', 180, 1, '2026-10-15', '정리', 'filled', 1, 210,"
      " 't', 't')")  # fmt: skip
    기록 = job.run(c, "US", _kst("21:27"), session=_UsSess(holdings=[kv.Holding("AAPL", 3, 3, 200.0)]),
                   clock=lambda: _kst("21:27"))  # fmt: skip
    assert 기록["realized_updated"] == 0
    assert c.q("SELECT realized_krw FROM paper_broker_orders WHERE reason = '정리' AND status = 'filled'") == [(None,)]
    기록 = job.run(c, "US", _kst("21:27"), session=_UsSess(holdings=[kv.Holding("AAPL", 2, 2, 200.0)]),
                   clock=lambda: _kst("21:27"))  # fmt: skip
    assert 기록["realized_updated"] == 1
    # 210 × 1,410 − 200 × 1,400 = 16,100 = 주가 10 × 1,410 + 환차 200 × 10
    assert c.q("SELECT realized_krw, realized_fx_krw FROM paper_broker_orders WHERE reason = '정리'"
               " AND status = 'filled'") == [(pytest.approx(16_100.0), pytest.approx(2_000.0))]  # fmt: skip
