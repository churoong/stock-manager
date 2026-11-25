"""KIS 실현손익 보유 구간이 닫히는 길 (docs/infra.md 25.2349·2350·2351 — 11-24 결함 루프 건너뛴 ⓑ). ① KIS 0 기준점
② 기록 0 이고 마지막 매수 뒤 기준점을 거쳤으며 그 뒤 기록 밖 매수가 없음.

기록 보유가 0 이 되는 것만으로 새 구간을 열면, 그 뒤 KIS > 기록이 드러나도 앞 매도의 값이 영구히 남았다.
"""

from __future__ import annotations

from batch.services import broker_mirror as bm
from tests.test_broker_realized_1698 import _o

# 25.1701 S1 과 같은 행 — 10주 @100(기록) + 기록 밖 5주가 실제로 찬 채 10주 @120 매도(기록 0, KIS 5), 3주 @110 다시 삼,
# 8주 @125 매도. KIS 평균 원가로 맞는 첫 매도 값은 +100 — 기록 원가로는 +200
_행 = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(3, "2026-10-15", "sell", 10, 120.0),
      _o(4, "2026-10-16", "buy", 3, 110.0), _o(5, "2026-10-19", "sell", 8, 125.0)]  # fmt: skip


def test_10_16_기준점이_빠져도_앞_매도를_모름으로() -> None:
    # 10-16 개장 전 기준점이 빠지고(개장 뒤 덮어씀·잔고 잘림) 10-19 기준점만 — 그날 KIS 8 > 기록 3 이 드러남
    r = bm.realized(_행, lambda d: None, {}, snapshots=[("2026-10-19", {"C1": 8})])
    assert r[3] == (None, None)


def test_기준점이_아예_없어도_뒤에_기록보다_많이_팔면_앞_매도도_모름() -> None:
    r = bm.realized(_행, lambda d: None, {}, snapshots=[])
    assert r[3] == (None, None) and r[5] == (None, None)


def test_KIS_0_을_확인한_한_바퀴는_그대로() -> None:
    """닫는 길 ① KIS 0 기준점 — 깨끗한 한 바퀴(10-16 KIS 0)는 뒤 어긋남이 건드리지 않는다(25.1701 S2 와 같은 잣대)"""
    행 = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(2, "2026-10-15", "sell", 10, 120.0),
         _o(3, "2026-10-19", "buy", 3, 110.0), _o(4, "2026-10-20", "sell", 5, 125.0)]  # fmt: skip
    r = bm.realized(행, lambda d: None, {}, snapshots=[("2026-10-16", {})])
    assert r[2] == (200.0, 0.0) and r[4] == (None, None)


def test_직전_기준점이_수량을_확인한_바퀴는_닫는다() -> None:
    """25.2350 (25.2349 교차검증 [권고]) — 10-15 개장 전 KIS 10 = 기록 10 을 확인했고 그 뒤 매수 없이 다 팔았으면 그
    바퀴는 맞다(+200). 25.2349 는 이것도 다음 KIS 0 까지 모름으로 만들어 이미 적힌 값을 소급해 NULL 로 덮었다"""
    행 = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(2, "2026-10-15", "sell", 10, 120.0),
         _o(3, "2026-10-15", "buy", 3, 110.0)]  # fmt: skip
    assert bm.realized(행, lambda d: None, {}, snapshots=[("2026-10-15", {"C1": 10}), ("2026-10-16", {})])[2] == (200.0, 0.0)
    # 같은 확인 뒤 뒤에 어긋남(KIS 8 > 기록 3)이 드러나도 앞 바퀴는 그대로 — 새 바퀴만 모름
    r = bm.realized(행 + [_o(4, "2026-10-19", "sell", 8, 125.0)], lambda d: None, {},
                    snapshots=[("2026-10-15", {"C1": 10}), ("2026-10-19", {"C1": 8})])  # fmt: skip
    assert r[2] == (200.0, 0.0) and r[4] == (None, None)


def test_확인_뒤_매수가_있었으면_닫지_않는다() -> None:
    """기준점 뒤 매수가 섞였으면 그 수량은 확인되지 않았다 — ⓑ 재현과 같은 잣대로 앞 매도도 모름"""
    행 = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(2, "2026-10-15", "buy", 5, 100.0),
         _o(3, "2026-10-16", "sell", 15, 120.0), _o(4, "2026-10-16", "buy", 3, 110.0),
         _o(5, "2026-10-19", "sell", 8, 125.0)]  # fmt: skip
    r = bm.realized(행, lambda d: None, {}, snapshots=[("2026-10-15", {"C1": 10}), ("2026-10-19", {"C1": 8})])
    assert r[3] == (None, None)


def test_기준점_뒤_기록_밖_매수가_있었으면_닫지_않는다() -> None:
    """25.2351 (25.2350 교차검증 [필수] 1) — 10-15 개장 전 KIS 10 = 기록 10 을 확인한 뒤 같은 날 기록 밖 5주(모름으로 닫힘,
    실제로 참) → 10-16 기준점 빠짐, 10주 @120 매도 → 10-19 3주 다시 삼 → 10-20 KIS 8 > 기록 3. `산뒤` 만 보면 ② 가 앞
    바퀴를 닫아 +200(참값 +100)이 남았다"""
    행 = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(3, "2026-10-16", "sell", 10, 120.0),
         _o(4, "2026-10-19", "buy", 3, 110.0), _o(5, "2026-10-20", "sell", 3, 125.0)]  # fmt: skip
    스냅 = [("2026-10-15", {"C1": 10}), ("2026-10-20", {"C1": 8})]
    밖 = [{"trade_date": "2026-10-15", "stock_id": 1, "code": "C1"}]
    assert bm.realized(행, lambda d: None, {}, 스냅, maybe_buys=밖)[3] == (None, None)
    # 기록 밖 매수가 없으면 같은 행에서 앞 바퀴는 닫힌다(25.2350 — 확인된 수량)
    assert bm.realized(행, lambda d: None, {}, 스냅)[3] == (200.0, 0.0)
    # 기록 밖 매수가 확인 기준점 **앞**이면(그 기준점이 이미 셌다) 닫는다
    앞 = [{"trade_date": "2026-10-14", "stock_id": 1, "code": "C1"}]
    assert bm.realized(행, lambda d: None, {}, 스냅, maybe_buys=앞)[3] == (200.0, 0.0)


def test_일일_배치가_기록_밖_매수를_읽어_넘긴다() -> None:
    """`update_realized` 가 모름·응답 없음·접수 매수(체결 0)를 읽어 `maybe_buys` 로 넘긴다(25.2351)"""
    from batch.jobs import broker_mirror as job
    from tests.test_broker_realized_1694 import _Client, _Rs

    class _밖(_Client):
        def execute(self, sql: str, args: list | None = None) -> _Rs:
            if sql == job.REALIZED_MAYBE_BUYS_SQL:
                return _Rs([["2026-10-15", 1, "C1"]], ["trade_date", "stock_id", "code"])
            return super().execute(sql, args)

    orders = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(3, "2026-10-16", "sell", 10, 120.0),
              _o(4, "2026-10-19", "buy", 3, 110.0), _o(5, "2026-10-20", "sell", 3, 125.0)]  # fmt: skip
    for o in orders:
        o["realized_krw"], o["realized_fx_krw"] = 200.0, 0.0  # 지금 적힌 값
    c = _밖(orders)
    c.snapshots = [("2026-10-15", "2026-10-14T23:00:00+00:00", '[{"code": "C1", "qty": 10}]'),
                   ("2026-10-20", "2026-10-19T23:00:00+00:00", '[{"code": "C1", "qty": 8}]')]  # fmt: skip
    job.update_realized(c, "kis_vts", "KR")
    바뀜 = {x[1][2]: x[1][:2] for x in c.batches[0]}
    assert 바뀜[3] == [None, None]  # 기록 밖 매수가 있어 앞 바퀴도 모름
    for 조각 in ("side = 'buy'", "status IN ('sending', 'accepted')", "(status = 'unknown' AND market = 'KR')",
                 "COALESCE(filled_qty, 0) = 0"):  # fmt: skip
        assert 조각 in job.REALIZED_MAYBE_BUYS_SQL, 조각



def test_기준점도_행도_없는_날의_기록_밖_매수도_센다() -> None:
    """25.2352 (25.2351 교차검증 [권고]) — 앞 시험의 기록 밖 매수가 기준점 날이라, 날짜 고리에 그 날을 넣는 줄을 되돌려도
    지나갔다. 10-14 확인 기준점 뒤 행·기준점 없는 10-15 에 기록 밖 매수 → 10-16 다 팖 → 다시 삼 → 10-20 KIS 8 > 기록 3"""
    행 = [_o(1, "2026-10-13", "buy", 10, 100.0), _o(3, "2026-10-16", "sell", 10, 120.0),
         _o(4, "2026-10-19", "buy", 3, 110.0), _o(5, "2026-10-20", "sell", 3, 125.0)]  # fmt: skip
    스냅 = [("2026-10-14", {"C1": 10}), ("2026-10-20", {"C1": 8})]
    밖 = [{"trade_date": "2026-10-15", "stock_id": 1, "code": "C1"}]
    assert bm.realized(행, lambda d: None, {}, 스냅, maybe_buys=밖)[3] == (None, None)
    assert bm.realized(행, lambda d: None, {}, 스냅)[3] == (200.0, 0.0)


def test_다_판_뒤의_기록_밖_매수는_앞_바퀴를_열어_두지_않는다() -> None:
    """25.2352 (25.2351 교차검증 [권고]) — 앞 바퀴 원가를 흐릴 수 있는 것은 기록 보유가 0 이 되기 전의 기록 밖 매수뿐.
    다 판 뒤(10-17) 의 것까지 막으면 맞는 +200 이 소급해 모름이 됐다. 새 바퀴는 그대로 모름(KIS 8 > 기록 3)"""
    행 = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(3, "2026-10-16", "sell", 10, 120.0),
         _o(4, "2026-10-19", "buy", 3, 110.0), _o(5, "2026-10-20", "sell", 3, 125.0)]  # fmt: skip
    스냅 = [("2026-10-15", {"C1": 10}), ("2026-10-20", {"C1": 8})]
    r = bm.realized(행, lambda d: None, {}, 스냅, maybe_buys=[{"trade_date": "2026-10-17", "stock_id": 1, "code": "C1"}])
    assert r[3] == (200.0, 0.0) and r[5] == (None, None)


def test_기록_밖_매수와_밖_매도가_겹쳐_KIS_가_적으면_모름() -> None:
    """25.2352 (25.2351 교차검증 [권고], 탐침 P3) — 10-15 KIS 10 = 기록 10 확인, 같은 날 기록 밖 매수 5주 @200(참)과 기록
    밖 매도 7주(참), 10-16 KIS 8 < 기록 10, 10-17 8주 @150 매도. 기록 원가를 비율로 줄이면 +400(참값 약 +133)"""
    행 = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(2, "2026-10-17", "sell", 8, 150.0)]
    r = bm.realized(행, lambda d: None, {}, [("2026-10-15", {"C1": 10}), ("2026-10-16", {"C1": 8})],
                    maybe_buys=[{"trade_date": "2026-10-15", "stock_id": 1, "code": "C1"}])  # fmt: skip
    assert r[2] == (None, None)
    # 기록 밖 매수가 없으면 예전처럼 비율로 줄여 맞는 값(기록 밖 매도뿐)
    assert bm.realized(행, lambda d: None, {}, [("2026-10-15", {"C1": 10}), ("2026-10-16", {"C1": 8})])[2] == (400.0, 0.0)
