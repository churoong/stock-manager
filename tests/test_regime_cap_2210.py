"""45회차 (가) `regime_cap_v1` 순수 계산 — 손 계산 시험 (docs/factors.md 12.48).

계좌 Z1·`core_b_rc` 는 아직 잇지 않는다(12월 판정 실행 뒤) — 이 시험은 계산 모듈만 본다.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from batch.services import desk, trend
from batch.services import paper as pp
from batch.services import regime_cap as rc


def _평일(start: str, end: str) -> list[str]:
    d, e, out = date.fromisoformat(start), date.fromisoformat(end), []
    while d <= e:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _계열(start: str = "2024-10-01", end: str = "2025-10-31", 값: float = 100.0,
          바꿈: dict[str, float] | None = None) -> list[tuple[str, float]]:
    return [(d, (바꿈 or {}).get(d, 값)) for d in _평일(start, end)]


# ---------------------------------------------------------------- 월말 국면·묵음


def test_월말에만_국면을_본다_달_중간_급락은_무시():
    # 10-15 하루 50 — 매일 판정이면 약세, 끝난 월말(9-30 = 100 = SMA)은 강세
    s = _계열(바꿈={"2025-10-15": 50.0})
    assert trend.regime_from_closes("KOSPI", s, "2025-10-15").state == "bear"
    r = rc.month_end_regime("KOSPI", s, "2025-10-20")
    assert (r.state, r.date) == ("bull", "2025-09-30")
    prev, cur = rc.month_end_pair("KOSPI", s, "2025-10-20")
    assert rc.transition(prev, cur) is None


def test_월말_전환_강세에서_약세():
    # 10-31 = 90: SMA = (199×100 + 90)/200 = 99.95 > 90 → 약세. 직전 9-30 = 100 ≥ 100 → 강세
    s = _계열(바꿈={"2025-10-31": 90.0})
    prev, cur = rc.month_end_pair("KOSPI", s, "2025-11-03")
    assert (prev.state, prev.date) == ("bull", "2025-09-30")
    assert (cur.state, cur.date, cur.sma) == ("bear", "2025-10-31", pytest.approx(99.95))
    assert rc.transition(prev, cur) == rc.TO_BEAR
    # 같은 월말을 두 번 처리하지 않는다
    assert rc.should_act(prev, cur, None) == rc.TO_BEAR
    assert rc.should_act(prev, cur, "2025-10-31") is None


def test_월말_국면은_regime_from_closes_를_그대로_쓴다():
    s = _계열(바꿈={"2025-10-31": 90.0})
    직접 = trend.regime_from_closes("KOSPI", [x for x in s if x[0] <= "2025-10-31"], "2025-10-31")
    assert rc.month_end_regime("KOSPI", s, "2025-11-03") == 직접


def test_as_of_당일_행은_보지_않는다():
    # 11-03 행이 있어도(손 실행) 아침 배치와 같게 — 끝난 월말 10-31 이고 묵음도 10-31 기준
    s = _계열(바꿈={"2025-10-31": 90.0}) + [("2025-11-03", 10.0)]
    r = rc.month_end_regime("KOSPI", s, "2025-11-03")
    assert (r.state, r.date) == ("bear", "2025-10-31")
    # 10-24 뒤로 비었다가 as_of 당일 행만 있으면 — 묵음은 as_of 전 마지막 행(10-24)으로 10일 → 모름
    빈 = _계열(end="2025-10-24") + [("2025-11-03", 100.0)]
    r = rc.month_end_regime("KOSPI", 빈, "2025-11-03")
    assert r.state == "unknown" and "10일" in (r.note or "")


def test_묵음_7일은_판정_8일은_모름_전환_없음():
    s = _계열(바꿈={"2025-10-31": 90.0})  # 마지막 행 10-31(금)
    assert rc.month_end_regime("KOSPI", s, "2025-11-07").state == "bear"  # 7일
    r = rc.month_end_regime("KOSPI", s, "2025-11-08")  # 8일
    assert r.state == "unknown" and "8일" in (r.note or "")
    prev, cur = rc.month_end_pair("KOSPI", s, "2025-11-08")
    assert rc.transition(prev, cur) is None


def test_직전_월말이_모름이면_전환_아님():
    # 9월 첫 행(9-01)까지 200일 미만 → 8-29 월말 모름, 9-30 강세 → 전환 없음
    s = _계열(start="2024-12-01", end="2025-10-31")
    prev, cur = rc.month_end_pair("KOSPI", s, "2025-10-01")
    assert prev.state == "unknown" and cur.state == "bull"
    assert rc.transition(prev, cur) is None
    # 200일 미만 지금 월말도 모름
    assert rc.month_end_regime("KOSPI", s[:150], "2025-07-01").state == "unknown"


def test_전환_종류():
    def R(state: str, d: str) -> trend.Regime:
        return trend.Regime("KOSPI", state, d)

    assert rc.transition(R("bear", "2025-09-30"), R("bull", "2025-10-31")) == rc.TO_BULL
    assert rc.transition(R("bull", "2025-09-30"), R("bull", "2025-10-31")) is None
    assert rc.transition(R("bear", "2025-09-30"), R("bear", "2025-10-31")) is None
    assert rc.transition(R("bull", "2025-10-31"), R("bear", "2025-10-31")) is None  # 같은 달
    assert rc.transition(R("unknown", "2025-09-30"), R("bear", "2025-10-31")) is None


def test_국면별_덮개():
    assert rc.exposure_cap("bear") == 0.5
    assert rc.exposure_cap("bull") == 1.0
    assert rc.exposure_cap("unknown") is None


# ---------------------------------------------------------------- 감축


def _pos(sid: int, q: int, first: str = "2025-03-03", cost: float | None = None) -> pp.Position:
    return pp.Position(stock_id=sid, currency="KRW", quantity=q, cost=cost if cost is not None else 100.0 * q,
                       first_buy_date=first)


def test_감축_수량_손계산():
    assert [rc.trim_quantity(q) for q in (0, 1, 2, 7, 9, 10)] == [0, 0, 1, 3, 4, 5]


def test_덮개는_약세로_바뀐_지수의_보유에만():
    보유 = {1: _pos(1, 7), 2: _pos(2, 10), 3: _pos(3, 1), 4: _pos(4, 10)}
    지수 = {1: "KOSPI", 2: "KOSDAQ", 3: "KOSPI", 4: "KOSPI"}
    plan = rc.trim_plan(보유, 지수, {"KOSPI"}, prices={1: 1000.0}, selling={4})
    # KOSDAQ(2) 는 그대로, 1주(3)는 0 이라 줄 없음, 매도 플래그로 전량 파는 4 는 감축하지 않는다
    assert [(x["stock_id"], x["quantity"], x["keep"]) for x in plan] == [(1, 3, 4)]
    assert plan[0]["reason"] == "국면감축" and plan[0]["est_amount"] == 3000.0
    assert rc.trim_plan(보유, 지수, set()) == []


def test_감축_합은_초과_주식수와_정확히_같다():
    보유 = {1: _pos(1, 7), 2: _pos(2, 10), 3: _pos(3, 1), 4: _pos(4, 9), 5: _pos(5, 3)}
    지수 = {1: "KOSPI", 2: "KOSDAQ", 3: "KOSPI", 4: "KOSPI", 5: "KOSDAQ"}
    for code, 손 in (("KOSPI", 3 + 0 + 4), ("KOSDAQ", 5 + 1)):
        plan = rc.trim_plan(보유, 지수, {code})
        assert sum(x["quantity"] for x in plan) == rc.excess_shares(보유, 지수, code) == 손


# ---------------------------------------------------------------- 예약 현금


def test_감축_대금은_새_신호_매수에_쓰지_않는다():
    예약 = rc.reserve_after({}, trimmed={"KOSPI": 30_000.0})
    assert 예약 == {"KOSPI": 30_000.0}
    assert rc.signal_cash(100_000.0, 예약) == 70_000.0
    assert rc.signal_cash(20_000.0, 예약) == 0.0
    # size_buys 시가 일괄: 단가 10,000 × 1.10 = 11,000, 여유 1.01 — 현금 100,000 이면 9주, 예약 뺀 70,000 이면 6주
    배분 = [{"stock_id": 9, "amount": 100_000.0, "ref_price": 10_000.0}]
    전부, _ = pp.size_buys(배분, 100_000.0, 1.0, set(), "KRW", "open")
    뺀, _ = pp.size_buys(배분, rc.signal_cash(100_000.0, 예약), 1.0, set(), "KRW", "open")
    assert (전부[0]["quantity"], 뺀[0]["quantity"]) == (9, 6)


def test_예약_현금_되채움에_쓰고_풀기():
    예약 = rc.reserve_after({"KOSPI": 30_000.0}, refilled={"KOSPI": 12_000.0})
    assert 예약 == {"KOSPI": 18_000.0}
    assert rc.reserve_after(예약, refilled={"KOSPI": 50_000.0}) == {}  # 0 아래로 안 간다
    assert rc.reserve_after({"KOSPI": 5.0, "SP500": 7.0}, released={"KOSPI"}) == {"SP500": 7.0}


# ---------------------------------------------------------------- 되채움


_감축 = {1: {"index_code": "KOSPI", "date": "2025-11-03", "quantity": 3}}


def test_되채움은_강세_전환에만_tranche_는_인자():
    보유 = {1: _pos(1, 4)}
    # 단가 = 10,000 × 1.10 × 1.01 = 11,110 → 40,000 // 11,110 = 3주
    주문, 빠짐 = rc.refill_plan(보유, _감축, {"KOSPI"}, {1: 10_000.0}, 40_000.0, 1.0, tranche=9)
    assert 주문 == [{"stock_id": 1, "tranche": 9, "limit_price": None, "ref_price": 10_000.0, "quantity": 3,
                     "kind": "되채움", "reason": "되채움"}]
    assert 빠짐 == []
    # 강세 전환이 다른 지수거나 없으면 아무것도 사지 않는다
    assert rc.refill_plan(보유, _감축, {"KOSDAQ"}, {1: 10_000.0}, 40_000.0, 1.0, tranche=9) == ([], [])
    assert rc.refill_plan(보유, _감축, set(), {1: 10_000.0}, 40_000.0, 1.0, tranche=9) == ([], [])
    with pytest.raises(TypeError):
        rc.refill_plan(보유, _감축, {"KOSPI"}, {1: 10_000.0}, 40_000.0, 1.0, tranche=None)  # type: ignore[arg-type]


def test_되채움은_예약_현금_안에서만_모자라면_남긴다():
    # 30,000 // 11,110 = 2주 — 1주 모자람
    주문, 빠짐 = rc.refill_plan({1: _pos(1, 4)}, _감축, {"KOSPI"}, {1: 10_000.0}, 30_000.0, 1.0, tranche=9)
    assert 주문[0]["quantity"] == 2
    assert 빠짐 == [{"stock_id": 1, "wanted": 3, "quantity": 2, "short": 1, "why": "예약 현금 부족"}]


def test_되채움_상한_플래그_재매수_로트():
    # 상한 남은 2주
    주문, 빠짐 = rc.refill_plan({1: _pos(1, 4)}, _감축, {"KOSPI"}, {1: 10_000.0}, 1e9, 1.0, tranche=9, room={1: 2})
    assert 주문[0]["quantity"] == 2 and 빠짐[0]["why"] == "상한" and 빠짐[0]["short"] == 1
    # 매도 플래그·손절이 선 로트
    주문, 빠짐 = rc.refill_plan({1: _pos(1, 4)}, _감축, {"KOSPI"}, {1: 10_000.0}, 1e9, 1.0, tranche=9, flagged={1})
    assert 주문 == [] and 빠짐[0]["why"] == "매도 플래그·손절"
    # 감축 뒤 전량 매도·재매수(첫 매수일 12-01 > 감축일 11-03)
    주문, 빠짐 = rc.refill_plan({1: _pos(1, 4, first="2025-12-01")}, _감축, {"KOSPI"}, {1: 10_000.0}, 1e9, 1.0,
                              tranche=9)
    assert 주문 == [] and 빠짐[0]["why"] == "감축 뒤 재매수 로트"
    # 이미 다 판 로트
    assert rc.refill_plan({}, _감축, {"KOSPI"}, {1: 10_000.0}, 1e9, 1.0, tranche=9)[1][0]["why"] == "보유 없음"


def test_같은_날_신호_매수는_되채움_뒤_남은_몫만():
    assert rc.room_after_refill({1: 5, 2: 4}, [{"stock_id": 1, "quantity": 3}]) == {1: 2, 2: 4}
    assert rc.room_after_refill({1: 2}, [{"stock_id": 1, "quantity": 3}]) == {1: 0}


# ---------------------------------------------------------------- 노출 맞춘 비교


def test_고정_비중_대조는_평균_노출이_같다():
    e = [1.0, 0.5, 0.5, 1.0]
    s = [0.1, -0.1, 0.1, 0.0]
    ē = rc.mean_exposure(e)
    assert ē == 0.75
    assert rc.mean_exposure([ē] * len(e)) == ē
    # Π(1 + 0.75 s) − 1 = 1.075 × 0.925 × 1.075 × 1 − 1
    assert rc.fixed_weight_control(e, s) == pytest.approx(0.068953125)
    assert rc.fixed_weight_control([ē] * 4, s) == pytest.approx(rc.fixed_weight_control(e, s))


def test_스패닝_알파_손계산():
    e = [1.0, 0.5, 0.5, 1.0]
    # (0.25)(0.1) + (−0.25)(−0.1) + (−0.25)(0.1) + 0 = 0.025
    assert rc.spanning_alpha(e, [0.1, -0.1, 0.1, 0.0]) == pytest.approx(0.025)
    assert rc.spanning_alpha([0.75] * 4, [0.1, -0.1, 0.1, 0.0]) == 0.0


def test_노출_맞춘_비교_desk_꼴():
    날 = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"]
    b = list(zip(날, [100.0, 110.0, 99.0, 99.0], strict=True))
    # B 비중: 1, 0.5, 0.02(< 0.05 → 뺌)
    b현금 = [(날[0], 0.0, 100.0), (날[1], 55.0, 110.0), (날[2], 97.02, 99.0)]
    z = [(날[0], 100.0), (날[3], 95.0)]
    z현금 = [(날[0], 50.0, 100.0), (날[1], 0.0, 105.0), (날[2], 0.0, 100.0)]
    m = rc.matched_comparison(z, z현금, b, b현금)
    assert m is not None
    # s = [0.1/1, (99/110 − 1)/0.5 = −0.2], e = [0.5, 1], ē = 0.75
    assert (m["days"], m["skipped_days"]) == (2, 1)
    assert m["mean_exposure"] == pytest.approx(0.75)
    assert m["fixed_weight"] == pytest.approx(1.075 * 0.85 - 1)
    assert m["alpha"] == pytest.approx(-0.25 * 0.1 + 0.25 * -0.2)
    assert m["z1_twr"] == pytest.approx(-0.05)
    # 현금 행이 비면 모름
    assert rc.matched_comparison(z, z현금[:1], b, b현금) is None


# ---------------------------------------------------------------- 0단계 거름


def test_지수_대리_노출과_시점_선택_항():
    # 10-31 = 90 하루 −10% 뒤 줄곧 90. 11-03(11월 첫 행) 아침 약세 전환 → 그날부터 0.5, 그날 수익은 전날 노출 1
    s = _계열(end="2025-12-01", 바꿈={})
    s = [(d, 100.0 if d < "2025-10-31" else 90.0) for d, _c in s]
    p = rc.proxy_path("KOSPI", s)
    assert p["events"] == 1 and p["transitions"] == [("2025-10-31", rc.TO_BEAR)]
    날 = [d for d, _c in s]
    i = 날.index("2025-11-03")
    assert p["e_prev"][i - 1] == 1.0 and p["e_prev"][i] == 0.5  # e_prev[k] 는 날[k+1] 의 수익에 쓴 노출
    k = sum(1 for d in 날[1:] if d > "2025-11-03")
    n = len(날) - 1
    # 항 = (1 − ē)(−0.1), ē = 1 − 0.5k/n → −0.05 k / n
    assert rc.proxy_timing_term("KOSPI", s) == pytest.approx(-0.05 * k / n)
    assert rc.proxy_events("KOSPI", s) == 1


def test_거름_알파_가지():
    assert rc.proxy_filter({"KOSPI": 0.01, "SP500": 0.02}) is True
    assert rc.proxy_filter({"KOSPI": 0.01, "SP500": 0.0}) is False  # ≤ 0 이면 열지 않는다
    assert rc.proxy_filter({"KOSPI": 0.01, "SP500": None}) is False
    assert rc.proxy_filter({}) is False


# ---------------------------------------------------------------- 판정


def test_사건_수는_나라_월로_묶는다():
    t = [("KR", "2025-10-31", rc.TO_BEAR), ("KR", "2025-10-31", rc.TO_BEAR),  # KOSPI·KOSDAQ 같은 달 → 하나
         ("KR", "2025-12-31", rc.TO_BULL)]
    assert rc.bear_events(t) == 1
    assert rc.bear_events(t + [("US", "2025-10-31", rc.TO_BEAR)]) == 2


_좋은 = {"mdd_z1": -0.35, "mdd_b": -0.5, "twr_z1": 0.45, "twr_b": 0.5,
        "matched": {"fixed_weight": 0.45, "alpha": 0.0}, "events": 2}


def test_판정_경계는_모두_통과():
    assert rc.judge(**_좋은)["verdict"] == "통과"


@pytest.mark.parametrize("바꿈, 걸린", [
    ({"mdd_z1": -0.3500001}, "a_mdd"),       # |MDD_Z1| > 0.7 × |MDD_B|
    ({"twr_z1": 0.4499999, "matched": {"fixed_weight": 0.0, "alpha": 0.0}}, "b_twr"),
    ({"matched": {"fixed_weight": 0.4500001, "alpha": 0.0}}, "c1_fixed"),
    ({"matched": {"fixed_weight": 0.45, "alpha": -1e-9}}, "c2_alpha"),
])
def test_판정_경계_한_칸씩_탈락(바꿈, 걸린):
    r = rc.judge(**{**_좋은, **바꿈})
    assert r["verdict"] == "탈락" and r["why"] == 걸린


def test_판정_MDD_부호():
    # 음수 규약 — 덜 빠진 쪽(−0.1)이 통과, 양수(크기)로 넣으면 거부
    assert rc.judge(**{**_좋은, "mdd_z1": -0.1})["checks"]["a_mdd"] is True
    with pytest.raises(ValueError):
        rc.judge(**{**_좋은, "mdd_z1": 0.35, "mdd_b": 0.5})


def test_판정_불가_사건_2개_미만_재료_빔():
    assert rc.judge(**{**_좋은, "events": 1})["verdict"] == "판정 불가"
    assert rc.judge(**{**_좋은, "events": 0})["verdict"] == "판정 불가"
    assert rc.judge(**{**_좋은, "matched": None})["verdict"] == "판정 불가"
    assert rc.judge(**{**_좋은, "mdd_b": None})["verdict"] == "판정 불가"


# ---------------------------------------------------------------- 원가 경로 차이


def _체결(oid: int, side: str, d: str, price: float, q: int) -> pp.Fill:
    cash = -price * q if side == "buy" else price * q
    return pp.Fill(order_id=oid, account_id="Z1", stock_id=1, side=side, fill_date=d, price=price, quantity=q,
                   currency="KRW", fx_rate=1.0, fee=0.0, tax=0.0, slippage=0.0, cash_krw=cash)


def test_원가_경로_차이는_장부_평균_원가로_센다():
    b = pp.positions([_체결(1, "buy", "2025-03-03", 100.0, 10)])
    # Z1: 10주 @100 → 5주 감축 → 5주 @120 되채움 → 원가 500 + 600 = 1,100, 평균 110
    z = pp.positions([_체결(1, "buy", "2025-03-03", 100.0, 10), _체결(2, "sell", "2025-11-03", 90.0, 5),
                      _체결(3, "buy", "2026-02-02", 120.0, 5)])
    assert rc.avg_cost(b[1]) == 100.0 and rc.avg_cost(z[1]) == 110.0
    assert z[1].first_buy_date == "2025-03-03"  # 감축은 첫 매수일을 바꾸지 않는다 — 되채움 대상
    r = rc.cost_path_divergence(z, b, {1: ["목표도달", "기간초과", "손절"]})
    assert r == {"name": "원가 경로 차이", "lots": [1], "n_lots": 1, "target_stop_exits": 2}
    assert rc.cost_path_divergence(b, b)["n_lots"] == 0


# ---------------------------------------------------------------- 상수 = 정의처


def test_등록_상수는_정의처와_같다():
    assert rc.SMA_DAYS == trend.SMA_DAYS == 200
    assert rc.MAX_STALE_DAYS == trend.MAX_STALE_DAYS == 7
    assert rc.EXPOSURE_MIN == desk.EXPOSURE_MIN == 0.05
    # 0.5 는 계좌 상수 — 값은 설정 기본값과 같지만 설정을 읽지 않는다
    assert rc.BEAR_EXPOSURE == trend.DEFAULT_BEAR_FACTOR == 0.5
    assert (rc.MDD_RATIO, rc.TWR_MARGIN, rc.MIN_BEAR_EVENTS) == (0.7, 0.05, 2)
    assert pp.SELL_RULES["core"] >= rc.TARGET_STOP_CODES
    x판1 = desk.PREREG[("X", 1)]["judge"]
    assert "0.7 × 보유 MDD" in x판1 and "5%p" in x판1  # 다시 쓴 X·Y 판 1 첫 값
    글 = (Path(__file__).resolve().parents[1] / "docs" / "factors.md").read_text(encoding="utf-8")
    절 = 글[글.index("**(가) `regime_cap_v1` 사전 등록"):글.index("**(나) `etf_theme_v1` 사전 등록")]
    for 조각 in ("⌊q × (1 − 0.5)⌋", "MDD_Z1 ≥ 0.7 × MDD_B", "B − 5%p", "합해 2개 이상", "EXPOSURE_MIN` 0.05",
                 "'국면감축'", "'되채움'", "감축 대금은 예약 현금이다", "[확인필요: 0070 tranche 값 범위]"):
        assert 조각 in 절, 조각
