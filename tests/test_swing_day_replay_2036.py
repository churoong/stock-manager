"""S·T 계좌형 리플레이 (깔때기 ⑦-4, 25.2036) — 손으로 따라갈 수 있는 작은 자료로 체결·현금·까닭을 본다.

비용 0(`pp.Costs(0, 0, 0, 0)`)이라 체결가·수량·현금이 손 계산과 같아야 한다. 운영 `run_step` 과의 일치 시험은 ⑦-5.
"""

from __future__ import annotations

from batch.services import paper as pp
from batch.services import swing_day_replay as sdr
from tests.test_core_strategy_1532 import _영업일, _행

날 = _영업일("2026-03-02", 14)
캐시 = {sid: {"id": sid, "ticker": f"{sid:06d}", "name": f"종목{sid}", "sector": None, "country": "KR",
              "currency": "KRW", "asset_type": "stock"} for sid in (1, 2)}  # fmt: skip
설정 = {"max_weight_per_stock": 10.0, "max_weight_per_sector": 30.0, "min_order_amount": 0.0}
비용0 = pp.Costs(0.0, 0.0, 0.0, 0.0)


def _돌림(account: str, 종가: dict[int, list[float]], 시가: dict[int, list[float]], 신호날: dict[str, list[int]],
          **kw) -> sdr.STResult:  # fmt: skip
    prices = {sid: dict(zip(날, v, strict=True)) for sid, v in 종가.items()}
    opens = {sid: dict(zip(날, v, strict=True)) for sid, v in 시가.items()}

    def rows_at(view, d):  # noqa: ANN001 — 시점 창: cutoff 종가로 신호 행
        return [_행(sid, view.cutoff, prices[sid][view.cutoff]) for sid in 신호날.get(view.cutoff, [])]

    return sdr.simulate_account(account, rows_at=rows_at, prices=prices, opens=opens, dates=날,
                                costs_at=lambda _d: 비용0, stock_cache=캐시, settings=설정, deposit=10_000_000.0,
                                **kw)  # fmt: skip


def test_S_는_다음_장_시가에_사고_종가_목표를_넘은_다음_장_시가에_판다() -> None:
    # 종목 1: 날[0] 신호 → 날[1] 시가 1,000 에 산다. 날[3] 종가 1,050(+5%) → 날[4] 시가 1,060 에 판다
    종가 = {1: [1000, 1010, 1020, 1050, 1070] + [1070] * 9}
    시가 = {1: [1000, 1000, 1015, 1030, 1060] + [1070] * 9}
    r = _돌림("S", 종가, 시가, {날[0]: [1]})
    산, 판 = [f for f in r.fills if f.side == "buy"], [f for f in r.fills if f.side == "sell"]
    # 비중 상한 10% × 1,000만 = 100만 → 1,000원에 1,000주 이하(배분이 정한 수량)
    assert len(산) == 1 and 산[0].fill_date == 날[1] and 산[0].price == 1000.0 and 산[0].quantity > 0
    assert len(판) == 1 and 판[0].fill_date == 날[4] and 판[0].price == 1060.0 and 판[0].quantity == 산[0].quantity
    # 비용 0 — 현금 = 입금 + 수량 × (1,060 − 1,000)
    assert abs(r.curve[-1][1] * 10_000_000 - (10_000_000 + 산[0].quantity * 60)) < 1e-6
    # 노출(25.2046) — 산 날은 max(평가 1,010 × q, 산 금액 1,000 × q) ÷ 자본, 들고 가는 날은 평가 ÷ 자본, 모든 날 합 ≤ 1
    q = 산[0].quantity
    w = dict(r.weights)
    assert abs(w[날[1]][1] - 1010 * q / (10_000_000 + 10 * q)) < 1e-12
    assert abs(w[날[2]][1] - 1020 * q / (10_000_000 + 20 * q)) < 1e-12
    assert all(sum(x.values()) <= 1 + 1e-12 for x in w.values())
    # 판 뒤 쉼(5거래일) — 날[4]·[5] 에 신호가 또 나도 다시 안 산다
    r2 = _돌림("S", 종가, 시가, {날[0]: [1], 날[4]: [1], 날[5]: [1]})
    assert [f.fill_date for f in r2.fills if f.side == "buy"] == [날[1]]


def test_S_는_손절_종가_다음_장_시가에_판다() -> None:
    종가 = {1: [1000, 1000, 960] + [960] * 11}  # 날[2] 종가 −4% → 날[3] 시가
    시가 = {1: [1000, 1000, 990, 955] + [960] * 10}
    r = _돌림("S", 종가, 시가, {날[0]: [1]})
    판 = [f for f in r.fills if f.side == "sell"]
    assert [(f.fill_date, f.price) for f in 판] == [(날[3], 955.0)]


def test_T_는_시가에_사고_같은_장_종가에_판다_하루_끝_보유_0() -> None:
    종가 = {1: [1000, 1030, 1000] + [1000] * 11}
    시가 = {1: [1000, 1010, 1000] + [1000] * 11}
    r = _돌림("T", 종가, 시가, {날[0]: [1]})
    assert [(f.side, f.fill_date, f.price) for f in r.fills] == [("buy", 날[1], 1010.0), ("sell", 날[1], 1030.0)]
    assert pp.positions(r.fills) == {}
    q = r.fills[0].quantity
    assert abs(r.curve[-1][1] * 10_000_000 - (10_000_000 + q * 20)) < 1e-6
    # 노출·회전(25.2046) — 하루 끝 보유는 0 이지만 그날 산 금액(1,010 × q)이 노출, 회전은 (산 + 판) ÷ 2 ÷ 자본
    자본1 = 10_000_000 + q * 20
    w = dict(r.weights)[날[1]]
    assert abs(w[1] - 1010 * q / 자본1) < 1e-12 and sum(w.values()) <= 1
    assert abs(dict(r.turnover)[날[1]] - (1010 * q + 1030 * q) / 2 / 자본1) < 1e-12
    assert dict(r.weights)[날[2]] == {} and dict(r.turnover)[날[2]] == 0.0


def test_T_는_상한가_시가면_안_사고_짝_매도도_거둔다() -> None:
    종가 = {1: [1000, 1300] + [1300] * 12}
    시가 = {1: [1000, 1299] + [1300] * 12}  # 신호 날 종가 1,000 대비 +29.9%
    r = _돌림("T", 종가, 시가, {날[0]: [1]})
    assert r.fills == []
    첫날 = r.log[0]["expired"]
    assert any(k.startswith("상한가 시가") for k in 첫날) and 첫날["단타 짝 매수 안 참"] == 1


def test_S_매도가_정지로_막히면_미루고_다시_거래되는_날_판다() -> None:
    종가 = {1: [1000, 1000, 940] + [940] * 11}  # 날[2] 손절 → 날[3] 매도인데 날[3] 행이 없다(정지)
    시가 = {1: [1000, 1000, 950] + [945] * 11}
    r0 = {1: dict(zip(날, 종가[1], strict=True))}
    del r0[1][날[3]]
    o0 = {1: dict(zip(날, 시가[1], strict=True))}
    del o0[1][날[3]]
    rows = {날[0]: [1]}

    def rows_at(view, d):  # noqa: ANN001
        return [_행(sid, view.cutoff, r0[sid][view.cutoff]) for sid in rows.get(view.cutoff, [])]

    r = sdr.simulate_account("S", rows_at=rows_at, prices=r0, opens=o0, dates=날, costs_at=lambda _d: 비용0,
                             stock_cache=캐시, settings=설정, deposit=10_000_000.0)  # fmt: skip
    assert r.log[2]["deferred"] == 1
    assert [(f.fill_date, f.price) for f in r.fills if f.side == "sell"] == [(날[4], 945.0)]


def test_정지가_길면_미룬_횟수로_마지막_종가에_청산한다() -> None:
    """미룬 매도를 들고 가야 미룬 횟수가 쌓여 `HALT_LIQUIDATE_SESSIONS` 째에 마지막 종가로 청산한다 — 날마다 넘음을 다시
    내면(미룬 매도를 버리면) 횟수가 0 으로 돌아가 정지가 풀릴 때까지 못 판다(25.2036 되돌리기 ③)."""
    긴날 = _영업일("2026-03-02", 30)
    종가 = dict(zip(긴날, [1000.0, 1000.0, 940.0] + [940.0] * 27, strict=True))
    시가 = dict(zip(긴날, [1000.0, 1000.0, 950.0] + [945.0] * 27, strict=True))
    for d in 긴날[3:28]:  # 날[3]~[27] 정지(행 없음)
        del 종가[d], 시가[d]

    def rows_at(view, d):  # noqa: ANN001
        return [_행(1, view.cutoff, 종가[view.cutoff])] if view.cutoff == 긴날[0] else []

    r = sdr.simulate_account("S", rows_at=rows_at, prices={1: 종가}, opens={1: 시가}, dates=긴날,
                             costs_at=lambda _d: 비용0, stock_cache=캐시, settings=설정, deposit=10_000_000.0)  # fmt: skip
    판 = [f for f in r.fills if f.side == "sell"]
    # 날[3] 첫 미룸(횟수 1) … 20번째 시도 날[22] 에 마지막 종가(날[2] 940)로
    assert [(f.fill_date, f.price) for f in 판] == [(긴날[3 + pp.HALT_LIQUIDATE_SESSIONS - 1], 940.0)]


def test_판_뒤_쉼은_판단일_기준_5거래일이다() -> None:
    """운영 run_step 과 같이 쉼 시작 = 판단일(cutoff)에서 4장 앞(25.2039, 교차검증 [필수]): 날[4] 에 팔면 cutoff 날[8]
    (판 날 + 4장)은 막히고 cutoff 날[9](판 날 + 5장)에 사서 날[10] 에 찬다. 예전 체결일 기준은 날[8] 에 샀다."""
    종가 = {1: [1000, 1010, 1020, 1050, 1070] + [1070] * 9}
    시가 = {1: [1000, 1000, 1015, 1030, 1060] + [1070] * 9}
    r = _돌림("S", 종가, 시가, {날[0]: [1], 날[8]: [1], 날[9]: [1]})
    assert [f.fill_date for f in r.fills if f.side == "sell"][0] == 날[4]
    assert [f.fill_date for f in r.fills if f.side == "buy"] == [날[1], 날[10]]


def test_정지_청산은_그날까지의_마지막_종가다() -> None:
    """운영 `LAST_CLOSE_SQL`(date <= 그날)과 같이 그날 행의 종가까지 본다(25.2039, 교차검증 [권고]) — 청산 날 행의 종가가
    앞날과 다르면 그날 값."""
    긴날 = _영업일("2026-03-02", 30)
    종가 = dict(zip(긴날, [1000.0, 1000.0, 940.0] + [930.0] * 27, strict=True))
    시가 = dict(zip(긴날, [1000.0, 1000.0, 950.0] + [930.0] * 27, strict=True))
    청산날 = 긴날[3 + pp.HALT_LIQUIDATE_SESSIONS - 1]
    종가[청산날] = 920.0
    대금 = {1: {d: (0.0 if 긴날[3] <= d < 긴날[28] else 1e6) for d in 긴날}}  # 날[3]~[27] 거래량 0 정지

    def rows_at(view, d):  # noqa: ANN001
        return [_행(1, view.cutoff, 종가[view.cutoff])] if view.cutoff == 긴날[0] else []

    r = sdr.simulate_account("S", rows_at=rows_at, prices={1: 종가}, opens={1: 시가}, dates=긴날,
                             costs_at=lambda _d: 비용0, stock_cache=캐시, settings=설정, deposit=10_000_000.0,
                             turnover=대금)  # fmt: skip
    assert [(f.fill_date, f.price) for f in r.fills if f.side == "sell"] == [(청산날, 920.0)]


def test_판단일_행이_빠진_정지_뒤_첫날_하한가_시가면_매도를_미룬다() -> None:
    """등락은 그날 앞 **행이 있는 마지막 날** 종가로 잰다(25.2039·25.2040) — 판단일(날[3]) 행이 빠진 정지 뒤 첫날(날[4])
    시가가 그 앞 행(날[2] 940) 대비 −30% 하한가면 잠겨 미룬다. 예전엔 판단일 행이 없어 등락이 None 이라 그 값에 팔았다."""
    종가 = dict(zip(날, [1000.0, 1000.0, 940.0, 0.0, 658.0] + [700.0] * 9, strict=True))
    시가 = dict(zip(날, [1000.0, 1000.0, 950.0, 0.0, 658.0] + [700.0] * 9, strict=True))
    del 종가[날[3]], 시가[날[3]]  # 날[3] 행 없음(정지) — 날[2] 손절 → 날[3] 매도 미룸 → 날[4] 하한가 잠김 → 날[5] 팜

    def rows_at(view, d):  # noqa: ANN001
        return [_행(1, view.cutoff, 종가[view.cutoff])] if view.cutoff == 날[0] else []

    r = sdr.simulate_account("S", rows_at=rows_at, prices={1: 종가}, opens={1: 시가}, dates=날, costs_at=lambda _d: 비용0,
                             stock_cache=캐시, settings=설정, deposit=10_000_000.0)  # fmt: skip
    assert r.log[2]["deferred"] == 1 and r.log[3]["deferred"] == 1  # 날[3] 정지, 날[4] 하한가
    assert [(f.fill_date, f.price) for f in r.fills if f.side == "sell"] == [(날[5], 700.0)]
