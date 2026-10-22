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
    # KIS 에 기록 밖 주식이 남았을 수 있다 — 개장 전 잔고가 0 을 보이기 전까지 그 뒤 구간도 모름(25.1701)
    assert r[5] == (None, None)
    r2 = bm.realized([_o(1, "2026-10-14", "buy", 10, 100.0), _o(2, "2026-10-15", "sell", 8, 110.0),
                      _o(3, "2026-10-16", "sell", 7, 105.0),
                      _o(4, "2026-10-20", "buy", 1, 50.0), _o(5, "2026-10-21", "sell", 1, 60.0)], lambda d: None,
                     snapshots=[("2026-10-19", {})])  # fmt: skip
    assert r2[5] == (10.0, 0.0)  # 10-19 개장 전 KIS 0 — 원가가 새로 시작


def test_KIS_잔고와_기록_끝_수량이_다르면_마지막_구간은_모름() -> None:
    rows = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(2, "2026-10-15", "sell", 5, 110.0),
            _o(3, "2026-10-16", "buy", 4, 10.0, sid=2), _o(4, "2026-10-17", "sell", 4, 12.0, sid=2)]  # fmt: skip
    assert bm.realized(rows, lambda d: None) == {2: (50.0, 0.0), 4: (8.0, 0.0)}  # 잔고를 모르면 예전대로
    assert bm.realized(rows, lambda d: None, {"C1": 5}) == {2: (50.0, 0.0), 4: (8.0, 0.0)}  # 맞음
    # KIS 는 7주 — 기록에 없는 2주가 있다. 다 판 C2 를 KIS 도 0 으로 들면 그대로
    assert bm.realized(rows, lambda d: None, {"C1": 7}) == {2: (None, None), 4: (8.0, 0.0)}
    # KIS 가 **적게** 든 것(기록 밖 매도)은 원가를 흐리지 않는다 — 매도는 평균 원가를 바꾸지 않는다(25.1701)
    assert bm.realized(rows, lambda d: None, {"C1": 3}) == {2: (50.0, 0.0), 4: (8.0, 0.0)}
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
    """`run` 이 같은 실행에서 읽은 KIS 잔고로 견준다 — 기록 끝 2주에 KIS 2주면 적고, 3주면 모름으로 거둔다"""
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
    기록 = job.run(c, "US", _kst("21:27"), session=_UsSess(holdings=[kv.Holding("AAPL", 2, 2, 200.0)]),
                   clock=lambda: _kst("21:27"))  # fmt: skip
    assert 기록["realized_updated"] == 1
    # 210 × 1,410 − 200 × 1,400 = 16,100 = 주가 10 × 1,410 + 환차 200 × 10
    assert c.q("SELECT realized_krw, realized_fx_krw FROM paper_broker_orders WHERE reason = '정리'"
               " AND status = 'filled'") == [(pytest.approx(16_100.0), pytest.approx(2_000.0))]  # fmt: skip
    # 같은 날 다시 — KIS 가 3주(기록 밖 1주): 적어 둔 값을 모름으로 거둔다
    기록 = job.run(c, "US", _kst("21:27"), session=_UsSess(holdings=[kv.Holding("AAPL", 3, 3, 200.0)]),
                   clock=lambda: _kst("21:27"))  # fmt: skip
    assert 기록["realized_updated"] == 1
    assert c.q("SELECT realized_krw FROM paper_broker_orders WHERE reason = '정리' AND status = 'filled'") == [(None,)]


# ── 개장 전 잔고 기준점 (25.1701, 25.1698 교차검증 [권고] 1·2) ─────────────────────────────────────────


def test_앞_구간에서_시작된_어긋남도_다시_사면_풀리지_않는다() -> None:
    """S1 — 10주 @100(기록) + 5주 @130(`unknown` 으로 닫았지만 참). 10주 @120 매도로 기록은 0, KIS 는 5. 3주 @110 을
    다시 사고 8주 @125 매도. 마지막 구간만 보던 때는 다시 산 뒤 10주 매도가 200 으로 적혔다(KIS 평균 원가로는 100)"""
    rows = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(3, "2026-10-15", "sell", 10, 120.0),
            _o(4, "2026-10-16", "buy", 3, 110.0), _o(5, "2026-10-19", "sell", 8, 125.0)]  # fmt: skip
    r = bm.realized(rows, lambda d: None, {}, snapshots=[("2026-10-16", {"C1": 5}), ("2026-10-20", {})])
    assert r == {3: (None, None), 5: (None, None)}


def test_KIS_0_으로_확인한_구간은_뒤의_어긋남이_건드리지_않는다() -> None:
    """S2 — 깨끗하게 닫혀 10-16 개장 전 KIS 0 으로 확인한 구간(200)은, 뒤에 기록보다 많이 판 매도가 나와도 그대로"""
    rows = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(2, "2026-10-15", "sell", 10, 120.0),
            _o(3, "2026-10-19", "sell", 5, 130.0)]  # fmt: skip
    assert bm.realized(rows, lambda d: None, snapshots=[("2026-10-16", {})]) == {2: (200.0, 0.0), 3: (None, None)}
    # 0 을 확인하지 못했으면 같은 구간이라 모름(보수)
    assert bm.realized(rows, lambda d: None)[2] == (None, None)


def test_기록_밖_매도는_원가를_흐리지_않는다() -> None:
    """S8 — 4주 매도가 `unknown` 으로 닫혔는데 참(KIS 6, 기록 10). 그 뒤 6주 @120 매도는 평균 원가 100 그대로라 120 이
    맞다 — 예전엔 기록이 KIS 보다 많은 것도 어긋남으로 봐 영구히 모름이었다. KIS 0 인 날 기록을 0 으로 맞춰 새로 산 것도 센다"""
    rows = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(3, "2026-10-16", "sell", 6, 120.0),
            _o(4, "2026-10-20", "buy", 2, 50.0), _o(5, "2026-10-21", "sell", 2, 55.0)]  # fmt: skip
    r = bm.realized(rows, lambda d: None, {}, snapshots=[("2026-10-16", {"C1": 6}), ("2026-10-19", {})])
    assert r == {3: (120.0, 0.0), 5: (10.0, 0.0)}
    # KIS 0 확인이 없으면 기록에 남은 4주(유령)가 새 매수 원가에 섞인다 — 2주 매도는 기록 6주 가운데서 판 것
    assert bm.realized(rows, lambda d: None)[5] != (10.0, 0.0)


def test_같은_날_매수_먼저가_기록_밖_보유를_가리지_않는다() -> None:
    """S5 — 개장 전 KIS 에 기록 밖 5주가 있는 날 5주 사고 5주 팔았다 — 매수 먼저 규칙으로는 0, KIS 평균 원가로는 다르다"""
    rows = [_o(1, "2026-10-15", "buy", 5, 100.0), _o(2, "2026-10-15", "sell", 5, 100.0)]
    assert bm.realized(rows, lambda d: None, snapshots=[("2026-10-15", {"C1": 5})]) == {2: (None, None)}
    assert bm.realized(rows, lambda d: None, snapshots=[("2026-10-15", {})]) == {2: (0.0, 0.0)}


def test_개장_뒤에_덮어쓴_잔고는_기준점이_아니다() -> None:
    """국내 10-15 개장 09:00 KST(00:00 UTC) — 08:27 KST 에 읽은 것만. 예전 꼴 보유 칸·코드 없는 칸도 읽는다"""
    rows = [
        {"as_of": "2026-10-15", "fetched_at": "2026-10-14T23:27:00+00:00",
         "holdings_json": '[{"id": 1, "code": "005930", "qty": 3}, {"id": 2, "code": "005930", "qty": 2}]'},
        {"as_of": "2026-10-16", "fetched_at": "2026-10-16T01:00:00+00:00", "holdings_json": '[{"code": "A", "qty": 1}]'},
        {"as_of": "2026-10-17", "fetched_at": "2026-10-17T05:00:00+00:00", "holdings_json": "[]"},  # 토요일 — 장 없음
        {"as_of": "2026-10-19", "fetched_at": "2026-10-18T23:00:00", "holdings_json": '[["000660", 4, 100.0], [7, 1, 1.0]]'},
    ]  # fmt: skip
    assert job._개장전_잔고(rows, "KR") == [("2026-10-15", {"005930": 5}), ("2026-10-17", {}),
                                        ("2026-10-19", {"000660": 4})]  # fmt: skip
