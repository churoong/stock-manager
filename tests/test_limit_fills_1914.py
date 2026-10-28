"""25.1914 (전략 깔때기 ④ 분할 지정가 체결) — 리플레이의 매수를 운영 가상모의투자처럼 3회 분할 지정가로 체결한다
(docs/funnel.md 4장 ④, docs/backtest.md 10장 "2단계에 3회 분할 지정가를 종가로 재현").

지키는 것
- 엔진(`bt.simulate` + `bt.LedgerOrders.limits`): 회차 셋 가운데 그날 종가 ≤ 지정가 인 것만(0·1·2·3개, 같을 때도 찬다)
  **지정가에** 산다 — 자본·비중·현금을 손으로 센다. 안 찬 회차는 그날로 사라진다(하루짜리). 시세 없는 날(정지)은
  아무것도 안 찬다. 지정가 = 종가 한 회차는 예전 `buys` 와 같다. 잘못된 회차는 크게 멈춘다
- 전략(`make_core_strategy(…, limit_fills=True)`): 회차 변형만 켜진다(R1·R2 는 그대로). 장부(`lots`)는 엔진이 실제로 채운
  회차만 — 원가 = 찬 회차 지정가의 주식 수 평균 × (1 + 매수 수수료), 하나도 안 차면 로트가 없다
- 끝까지(1532 합성 자료, core_b): 지정가 모드가 전량 모드보다 덜 산다(일부만 찬 날이 있다) · 장부 = 엔진 보유 날마다 ·
  새 로트의 주식 수가 찬 회차 수량과 같다
- 운영과 일치(1910 합성 자료, core_b, 비용 0): 운영 `run_step` 이 낸 회차를 **운영 체결 판정이 스스로** 채우게 두고(1910 의
  허용 ① "엔진 체결 베낌" 없이) 날마다 찬 회차·보유 수량·주당 원가가 리플레이와 같다
"""

from __future__ import annotations

import json
import math
from dataclasses import replace as dc_replace
from datetime import date, datetime
from functools import cache

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import backtest as bt
from batch.services import core_backtest as cb
from batch.services import core_strategy as cs
from batch.services import paper as pp
from tests import test_core_strategy_1532 as t32
from tests import test_replay_parity_1910 as t10
from tests.test_paper_job_1445 import 설정

# ── 엔진: 손으로 세는 회차 ───────────────────────────────────────────────────

#: (더 살 비중, 지정가) — 운영 회차 40/30/30 의 꼴
회차 = ((0.04, 100.0), (0.03, 98.0), (0.03, 96.0))
날 = t32._영업일("2024-01-02", 4)


def _엔진(종가: list[float | None], 주문: dict[int, bt.LedgerOrders], costs: bt.Costs | None = None) -> bt.BacktestResult:
    """종목 1 하나. `주문` = {날 번호: 장부 주문} — 그 밖의 날은 빈 주문."""
    prices = {1: {날[k]: p for k, p in enumerate(종가) if p is not None}}
    return bt.simulate(prices, 날[: len(종가)], 날[: len(종가)],
                       lambda d, _v: 주문.get(날.index(d), bt.LedgerOrders()), costs or bt.Costs.zero(),
                       has_delisted=True)  # fmt: skip


@pytest.mark.parametrize(("종가1", "찬수"), [(101.0, 0), (99.0, 1), (97.0, 2), (96.0, 3), (90.0, 3)])
def test_회차는_그날_종가_이하인_것만_지정가에_찬다(종가1: float, 찬수: int) -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — 첫날 100 · 판단은 둘째 날(cutoff = 첫날, 전부 현금). 둘째 날 종가가
    101 이면 0개, 99 면 1회차(100)만, 97 이면 1·2회차, 96(3회차 지정가와 같다 — 운영 "종가 ≤ 회차 가격")·90 이면 셋 다.
    찬 회차는 지정가에 산다: 낸 돈 Σw, 주식 수 Σ w/지정가, 둘째 날 가치 = 주식 수 × 종가, 남은 현금 = 1 − Σw."""
    r = _엔진([100.0, 종가1, 95.0, 95.0], {1: bt.LedgerOrders(limits={1: 회차})})
    찬 = 회차[:찬수]
    낸 = sum(w for w, _ in 찬)
    주식 = sum(w / 가 for w, 가 in 찬)
    자본1 = 1 - 낸 + 주식 * 종가1
    assert dict(r.curve)[날[1]] == pytest.approx(자본1, rel=1e-12)
    reb = r.rebalances[1]
    assert reb.weights == ({1: pytest.approx(주식 * 종가1 / 자본1, rel=1e-12)} if 찬수 else {})
    assert 1 - sum(reb.weights.values()) == pytest.approx((1 - 낸) / 자본1, rel=1e-12)  # 안 찬 몫은 현금
    assert reb.turnover == pytest.approx(낸 / 2)  # 회전은 낸 돈(운영 수수료도 지정가 × 수량에)
    # 다음 날 — 주식 수 그대로, 현금 그대로(안 찬 회차가 이월되지 않는다)
    assert dict(r.curve)[날[2]] == pytest.approx(1 - 낸 + 주식 * 95.0, rel=1e-12)


def test_예_손으로_센_값() -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — 종가 97 에 1·2회차: 0.04/100 + 0.03/98 = 0.000706122… 주,
    가치 0.068493…, 자본 0.93 + 0.068493 = 0.998493…(지정가 − 종가 만큼 체결 순간 손실)."""
    r = _엔진([100.0, 97.0, 97.0], {1: bt.LedgerOrders(limits={1: 회차})})
    주식 = 0.04 / 100 + 0.03 / 98
    assert 주식 == pytest.approx(0.000706122449, rel=1e-9)
    assert dict(r.curve)[날[1]] == pytest.approx(0.93 + 주식 * 97, rel=1e-12)
    assert dict(r.curve)[날[1]] == pytest.approx(0.998493877551, rel=1e-9)


def test_안_찬_회차는_하루로_사라진다() -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — 둘째 날 종가 101 이라 하나도 안 찼고, 셋째 날 90 으로 떨어져도 사지 않는다
    (운영 회차는 하루짜리 — 다음 날 주문이 없다)."""
    r = _엔진([100.0, 101.0, 90.0, 90.0], {1: bt.LedgerOrders(limits={1: 회차})})
    assert all(not x.weights for x in r.rebalances) and all(v == 1.0 for _d, v in r.curve)


def test_시세_없는_날은_아무것도_안_찬다() -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — 둘째 날 시세 없음(정지 — 뒤에 다시 거래) → 회차 셋 다 안 찬다(운영
    `judge`: 정지면 None → 매수 취소). 엔진은 "살 수 없어 뺀" 목록에 적는다. 다음 날 90 이어도 사지 않는다."""
    r = _엔진([100.0, None, 90.0, 90.0], {1: bt.LedgerOrders(limits={1: 회차})})
    assert r.rebalances[1].dropped == [1] and not r.rebalances[1].weights
    assert all(v == 1.0 for _d, v in r.curve)


def test_회차_비중은_판단_때_자본_대비_정해진_수량이다() -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — 운영 주문은 수량(주)이 정해져 있다. 종목 2 를 50% 든 채(첫날 판단)
    둘째 날 종목 2 가 두 배가 되어도, 둘째 날 낸 회차(cutoff = 첫날 자본 대비 0.1)는 첫날 자본 × 0.1 ÷ 지정가 주를 산다.
    `buys` 는 예전처럼 그날 자본 대비(두 배 커진 자본의 0.1)."""
    prices = {1: {날[0]: 100.0, 날[1]: 100.0, 날[2]: 100.0}, 2: {날[0]: 10.0, 날[1]: 20.0, 날[2]: 20.0}}
    주문 = {0: bt.LedgerOrders(buys={2: 0.5}), 1: bt.LedgerOrders(limits={1: ((0.1, 100.0),)})}
    r = bt.simulate(prices, 날[:3], 날[:3], lambda d, _v: 주문.get(날.index(d), bt.LedgerOrders()), bt.Costs.zero())
    # 첫날 자본 1(종목 2 0.5 + 현금 0.5) → 둘째 날 1.5. 산 주식 = 1 × 0.1 / 100 = 0.001 → 가치 0.1 = 둘째 날 자본의 1/15
    assert dict(r.curve)[날[1]] == pytest.approx(1.5)
    assert r.rebalances[1].weights[1] == pytest.approx(0.1 / 1.5)
    대조 = bt.simulate(prices, 날[:3], 날[:3],
                     lambda d, _v: {0: bt.LedgerOrders(buys={2: 0.5}), 1: bt.LedgerOrders(buys={1: 0.1})}.get(
                         날.index(d), bt.LedgerOrders()), bt.Costs.zero())  # fmt: skip
    assert 대조.rebalances[1].weights[1] == pytest.approx(0.1)


def test_지정가가_종가와_같은_한_회차는_예전_buys_와_같다() -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — 체결 순간 손실 0·수량 같음(cutoff 자본 = 그날 자본, 전부 현금) →
    비용이 붙어도 자본곡선·비중이 예전 `buys` 와 같다."""
    c = bt.Costs()
    가 = _엔진([100.0, 97.0, 99.0, 98.0], {1: bt.LedgerOrders(limits={1: ((0.1, 97.0),)})}, c)
    나 = _엔진([100.0, 97.0, 99.0, 98.0], {1: bt.LedgerOrders(buys={1: 0.1})}, c)
    assert [v for _d, v in 가.curve] == pytest.approx([v for _d, v in 나.curve], rel=1e-14)
    assert 가.rebalances[1].weights == pytest.approx(나.rebalances[1].weights, rel=1e-14)


def test_잘못된_회차는_크게_멈춘다() -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — 한 종목을 buys·limits 에 함께, 음수 비중, 0·NaN 지정가."""
    for 주문 in (bt.LedgerOrders(buys={1: 0.1}, limits={1: 회차}), bt.LedgerOrders(limits={1: ((-0.1, 100.0),)}),
                 bt.LedgerOrders(limits={1: ((0.1, 0.0),)}), bt.LedgerOrders(limits={1: ((0.1, math.nan),)})):
        with pytest.raises(ValueError):
            _엔진([100.0, 97.0, 97.0], {1: 주문})


# ── 전략: 장부는 엔진이 채운 회차만 ─────────────────────────────────────────────


class 회차시장(t32.가짜시장):
    """1532 의 가짜 판단기 — 신호 행의 회차 가격만 cutoff 종가의 배수(`배수`)로 바꾼다(운영 신호의 매수 구간 위·가운데·아래)."""

    def __init__(self, universe: list[dict], 신호: dict, 배수: tuple[float, float, float]) -> None:
        super().__init__(universe, 신호)
        self.배수 = 배수

    def at(self, view: bt.PriceView, day: str) -> cb.DayView:
        dv = super().at(view, day)
        rows = [dc_replace(r, tranche_plan=[{**t, "price": round(r.close * m, 2)}
                                            for t, m in zip(r.tranche_plan, self.배수, strict=True)])
                for r in dv.rows]  # fmt: skip
        return dc_replace(dv, rows=rows, pick_rows=rows)


def _전략(variant: str, 가격: list[float | None], *, limit_fills: bool = True, costs: bt.Costs | None = None,
          배수: tuple[float, float, float] = (1.0, 0.98, 0.96), 신호날: tuple[int, ...] = (0,)):  # fmt: skip
    """종목 1, 신호는 `신호날` cutoff 마다 10%. 날마다 맞춘 뒤 장부 = 엔진 보유를 본다."""
    날들 = t32._영업일("2024-01-05", len(가격))
    prices = {1: {날들[k]: p for k, p in enumerate(가격) if p is not None}}
    costs = costs or bt.Costs.zero()
    m = 회차시장(t32._종목들(1), {날들[k]: [(1, 10.0)] for k in 신호날}, 배수)
    s = cs.make_core_strategy(variant, m, sessions=날들, costs=costs, limit_fills=limit_fills)

    def 판단(day: str, view: bt.PriceView) -> bt.LedgerOrders:
        o = s(day, view)
        if view.stocks():
            assert set(s.lots) == set(view.holding or ()), f"{day}: 장부 {set(s.lots)} ≠ 엔진 {set(view.holding)}"
        return o

    r = bt.simulate(prices, 날들, 날들, 판단, costs, has_delisted=True)
    return s, r, 날들


@pytest.mark.parametrize(("종가1", "찬수"), [(101.0, 0), (100.0, 1), (99.0, 1), (97.0, 2), (96.0, 3), (95.0, 3)])
def test_전략_장부는_찬_회차의_지정가가_원가다(종가1: float, 찬수: int) -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — cutoff 종가 100 → 회차 100·98·96(40/30/30 의 10%). 산 날 종가 101·99·97·95
    에 0·1·2·3 회차가 찬다 — 종가 = 지정가(100·96)도 찬다(운영 `judge` "종가 ≤ 회차 가격", 25.1918 — 국내 1회차 = cutoff
    종가라 보합인 날이 이 경우다. 엔진·전략 한쪽만 `<` 면 장부와 엔진 보유가 갈린다). 로트 원가 = 찬 회차 지정가의 주식 수 평균(운영 `pp.positions` 의 cost ÷ quantity), 하나도 안
    차면 로트가 없고 엔진도 들지 않는다. 다음 날 엔진 비중으로 잰 주식 수 = 찬 회차 수량 합."""
    s, r, 날들 = _전략("core_b", [100.0, 종가1, 종가1, 종가1])
    lg = t32._날별(s)[날들[1]]
    낸 = lg.limits[1]
    assert [가 for _w, 가 in 낸] == [100.0, 98.0, 96.0] and lg.buys[1] == pytest.approx(0.1, rel=1e-6)
    assert [w for w, _l in 낸] == pytest.approx([0.04, 0.03, 0.03], rel=1e-6)
    찬 = 낸[:찬수]
    if not 찬수:
        assert not s.lots and not r.rebalances[1].weights
        return
    수량 = sum(w * cs.NOTIONAL_EQUITY / 가 for w, 가 in 찬)
    assert s.lots[1].unit_cost == pytest.approx(sum(w for w, _ in 찬) * cs.NOTIONAL_EQUITY / 수량, rel=1e-12)
    assert s.lots[1].first_buy == s.lots[1].last_buy == 날들[1]
    자본 = dict(r.curve)[날들[1]]
    assert r.rebalances[1].weights[1] * 자본 * cs.NOTIONAL_EQUITY / 종가1 == pytest.approx(수량, rel=1e-12)


def test_전략_원가에_매수_수수료() -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — 운영 `make_fill`: 지정가 매수는 슬리피지 없이 수수료만. 1·2회차가 차면
    원가 = (0.07 ÷ (0.04/100 + 0.03/98)) × (1 + 수수료)."""
    c = bt.Costs()
    s, _r, _날 = _전략("core_b", [100.0, 97.0, 97.0, 97.0], costs=c)
    assert s.lots[1].unit_cost == pytest.approx(0.07 / (0.04 / 100 + 0.03 / 98) * (1 + c.commission_pct / 100),
                                                rel=1e-6)  # fmt: skip


def test_전략_하루_지나면_회차가_없다() -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — 산 날 종가 101(안 참) → 다음 날 90 이어도 장부·엔진 모두 비었다."""
    s, r, _날 = _전략("core_b", [100.0, 101.0, 90.0, 90.0, 90.0])
    assert not s.lots and all(not x.weights for x in r.rebalances)


def test_전략_정지한_날_회차는_안_찬다() -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — 산 날 시세 없음(정지) → 회차 셋 다 안 차고 다음 날 사지 않는다."""
    s, r, 날들 = _전략("core_b", [100.0, None, 90.0, 90.0])
    assert 1 in t32._날별(s)[날들[1]].limits
    assert not s.lots and r.rebalances[1].dropped == [1] and all(not x.weights for x in r.rebalances)


def test_전략_더_사기는_찬_몫으로_원가를_평균한다() -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — 날 1 에 1회차만(종가 99) → 날 2 에 목표 10% 까지 더 사기(cutoff 99 → 회차
    99·97.02·95.04, 날 2 종가 97 → 1·2회차). 원가 = 두 날 찬 회차 전부의 주식 수 평균(운영 `positions`)."""
    s, r, 날들 = _전략("core_b", [100.0, 99.0, 97.0, 97.0, 97.0], 신호날=(0, 1))
    로그 = t32._날별(s)
    찬 = [*로그[날들[1]].limits[1][:1], *로그[날들[2]].limits[1][:2]]
    assert [가 for _w, 가 in 찬] == [100.0, 99.0, 97.02]
    # 날 2 회차 비중은 cutoff(날 1) 자본 대비 — 엔진과 같은 잣대라 수량 = w × 그 자본 × 명목 ÷ 지정가
    자본 = dict(r.curve)
    수량 = [로그[날들[1]].limits[1][0][0] * 자본[날들[0]] / 100.0,
           *(w * 자본[날들[1]] / 가 for w, 가 in 로그[날들[2]].limits[1][:2])]  # fmt: skip
    assert s.lots[1].unit_cost == pytest.approx(sum(q * 가 for q, (_w, 가) in zip(수량, 찬, strict=True)) / sum(수량),
                                                rel=1e-9)  # fmt: skip
    assert s.lots[1].last_buy == 날들[2]
    assert r.rebalances[2].weights[1] * 자본[날들[2]] / 97.0 == pytest.approx(sum(수량), rel=1e-12)


def test_시가_일괄_변형은_켜도_그대로다() -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — R1·R2 는 회차가 없다(entry open). 켜도 꺼짐과 같은 곡선·장부."""
    for v in ("core_r1", "core_r2"):
        assert not cs.make_core_strategy(v, t32.가짜시장([], {}), sessions=[], limit_fills=True).limit_fills
    for v in ("core_b", "core_g", "core_h"):
        assert cs.make_core_strategy(v, t32.가짜시장([], {}), sessions=[], limit_fills=True).limit_fills
        assert not cs.make_core_strategy(v, t32.가짜시장([], {}), sessions=[]).limit_fills  # 기본 꺼짐


# ── 끝까지: 1532 합성 자료 ────────────────────────────────────────────────────


@cache
def _끝까지(limit_fills: bool) -> tuple:
    universe, prices, turn, snaps, idx = t32._자료()
    s = cs.make_core_strategy("core_b", cb.CoreMarket(universe, snaps, idx), sessions=t32.날들, costs=bt.Costs(),
                              limit_fills=limit_fills)  # fmt: skip
    보유: dict[str, set[int]] = {}

    def 판단(day: str, view: bt.PriceView) -> bt.LedgerOrders:
        o = s(day, view)
        assert set(s.lots) == set(view.holding or ()), f"{day}: 전략 장부와 엔진 보유가 다르다"
        보유[day] = set(s.lots)
        return o

    r = bt.simulate(prices, t32.모의, t32.모의, 판단, bt.Costs(), turnover=turn, has_delisted=True)
    return s, r, prices


def test_끝까지_지정가_모드는_덜_사고_장부가_엔진과_같다() -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — 1532 합성 자료(정지·상장폐지 포함)로 core_b 를 끝까지. 날마다 장부 =
    엔진 보유(`_끝까지` 가 단언). 낸 회차 가운데 안 찬 것·일부만 찬 날이 있고, 찬 회차 수 < 낸 회차 수, 산 비중 합이
    전량 모드보다 작다. 새 로트(엔진이 다음 날 처음 든 종목)의 주식 수 = 찬 회차 수량 합(엔진 비중 × 자본 ÷ 종가)."""
    s, r, prices = _끝까지(True)
    전량, 전량r, _p = _끝까지(False)
    assert not any(lg.limits for lg in 전량.log)
    자본 = dict(r.curve)
    날번호 = {d: k for k, d in enumerate(t32.모의)}
    낸수 = 찬수 = 일부 = 안참 = 새로트 = 0
    산비중 = 0.0
    for lg in s.log:
        k = 날번호[lg.day]
        for sid, 회차들 in lg.limits.items():
            종가 = prices[sid].get(lg.day)
            찬 = [(w, 가) for w, 가 in 회차들 if 종가 is not None and 종가 <= 가]
            낸수, 찬수 = 낸수 + len(회차들), 찬수 + len(찬)
            일부 += 0 < len(찬) < len(회차들)
            안참 += not 찬
            산비중 += sum(w for w, _ in 찬)
            if 찬 and sid not in lg.held and sid in r.rebalances[k].weights:
                # 새로 산 종목 — 엔진 주식 수 = Σ w × cutoff 자본 ÷ 지정가 (명목 1 자본 단위)
                주식 = r.rebalances[k].weights[sid] * 자본[lg.day] / 종가
                cut = 자본.get(t32.모의[k - 1], 1.0) if k else 1.0
                assert 주식 == pytest.approx(sum(w * cut / 가 for w, 가 in 찬), rel=2e-3), (lg.day, sid)
                새로트 += 1
    assert 낸수 > 찬수 > 0 and 일부 >= 5 and 안참 >= 5 and 새로트 >= 5, (낸수, 찬수, 일부, 안참, 새로트)
    assert 산비중 < sum(sum(lg.buys.values()) for lg in 전량.log)
    # 1532 의 하루 정지(종목 2, 모의 247) — 그날 낸 회차 셋이 하나도 안 차고 엔진은 "살 수 없어 뺀" 목록에 적는다
    정지날 = next(lg for lg in s.log if lg.day == t32.모의[247])
    assert len(정지날.limits[2]) == 3 and 2 not in 정지날.held
    assert r.rebalances[247].dropped == [2] and 2 not in r.rebalances[247].weights


# ── 운영과 일치: 1910 합성 자료, 운영 체결 판정이 스스로 채운다 ───────────────────────


@cache
def _리플레이() -> tuple:
    universe, prices, turn, snaps, idx = t10._자료()
    s = cs.make_core_strategy("core_b", cb.CoreMarket(universe, snaps, idx), sessions=t10.날들, costs=bt.Costs.zero(),
                              buy_fee_pct=0.0, limit_fills=True)  # fmt: skip
    재료: dict[str, tuple[str, list]] = {}
    장부: dict[str, dict[int, float]] = {}
    원래 = s.market.at

    def 엿봄(view: bt.PriceView, day: str | None = None) -> cb.DayView:
        dv = 원래(view, day)
        재료[dv.day] = (dv.cutoff, list(dv.rows))
        return dv

    s.market.at = 엿봄  # type: ignore[method-assign]

    def 판단(day: str, view: bt.PriceView) -> bt.LedgerOrders:
        o = s(day, view)
        장부[day] = {sid: lot.unit_cost for sid, lot in s.lots.items()}  # 판단 직전 장부(맞춘 뒤)
        return o

    r = bt.simulate(prices, t10.모의, t10.모의, 판단, bt.Costs.zero(), turnover=turn, has_delisted=True)
    return s, r, 재료, 장부


@cache
def _운영() -> dict[str, dict]:
    """운영 `run_step` 을 리플레이 결정일마다(trade_date = cutoff). 1910 과 달리 운영이 낸 회차를 **운영 체결 판정**
    (`fill_orders` — 다음 `run_step` 첫머리)이 그날 종가로 스스로 채운다. 결정일 d 에 낸 회차는 다음 결정일의 실행이 판정한다.
    {체결일: {"fills": {(종목, 지정가, 수량)}, "orders": {(종목, 지정가, 수량)}, "pos": {종목: (수량, 원가)}}}"""
    s, r, 재료, _장부 = _리플레이()
    aid = cs.VARIANTS["core_b"].account
    c = t10._db(aid, {"kr_buy_pct": 0.0, "kr_sell_pct": 0.0})
    for d in t10.날들[: t10.W]:
        t10._시세넣기(c, d)
    mp = pytest.MonkeyPatch()
    out: dict[str, dict] = {}
    try:
        mp.setattr(job, "frozen_ids", lambda *_a, **_k: set())  # 1910 허용 ④
        # 비용 0 판 — 운영 시장가 매도의 슬리피지(설정 칸 없음, 0.10%)·시행일 거래세도 0 으로(엔진 `Costs.zero` 와 같게).
        # 엔진은 비용을 회전율에 매겨 지정가 매수에도 슬리피지를 물린다(`bt.Costs.rebalance_cost`) — 비용 판의 차이는
        # 1910 허용 ⑨ 와 같은 엔진 비용 장부 몫이라 여기서는 견주지 않는다
        mp.setattr(job, "costs_for", lambda *_a, **_k: pp.Costs(buy_fee_pct=0.0, sell_fee_pct=0.0, sell_tax_pct=0.0,
                                                                 slippage_pct=0.0))  # fmt: skip
        for day in t10.모의[:-1]:
            cutoff, rows = 재료[day]
            오늘 = date.fromisoformat(day)
            mp.setattr(cal, "local_today", lambda _m=None, _d=오늘: _d)
            job.run_step(c, market="KR", trade_date=cutoff, late=False, signal_rows=rows, settings=설정,
                         price_series={}, series_unknown={}, allocate=True,
                         now=datetime.fromisoformat(f"{day}T00:30:00+00:00"))  # fmt: skip
            out[day] = {"orders": set(c.q(
                "SELECT stock_id, limit_price, quantity FROM paper_orders WHERE account_id = ? AND decided_on = ?"
                " AND side = 'buy'", [aid, cutoff]))}  # fmt: skip
            if cutoff in out:  # 어제 낸 회차의 판정은 오늘 실행 첫머리에 끝났다
                out[cutoff]["fills"] = set(c.q(
                    "SELECT f.stock_id, o.limit_price, f.quantity FROM paper_fills f JOIN paper_orders o"
                    " ON o.id = f.order_id WHERE f.account_id = ? AND f.fill_date = ? AND f.side = 'buy'",
                    [aid, cutoff]))  # fmt: skip
                체결 = [f for f in job._fills(c)[0] if f.account_id == aid]
                out[cutoff]["pos"] = {sid: (p.quantity, p.cost / p.quantity) for sid, p in pp.positions(체결).items()}
                out[cutoff]["cash"] = pp.cash(job._flows(c)[0], 체결)
            t10._시세넣기(c, day)
    finally:
        mp.undo()
    return out


#: 정수 주 — 운영은 회차마다 수량을 내림(`size_buys`), 리플레이 비중은 명목 자본(1e12)의 실수. 2026-10-28 실측 최대
#: 낸 회차 1.13주·보유 1.42주(696 회차, 148 거래일) — 1910 허용 ③ 과 같은 뿌리
ALLOW_SHARES = 2.0
#: 주당 원가·현금 비중 — 2026-10-28 실측 최대 3.7e-8·4.5e-8(정수 주 내림이 비중에 남는 자리, 1910 허용 ③). 1910 허용 ③
#: 과 같은 2e-7(실측 약 4.4배, 25.1918 — 처음 1e-6 은 실측 22배라 25.1913 의 잣대와 어긋났다)
ALLOW_REL = 2e-7


def test_운영_체결_판정과_찬_회차·보유·원가가_같다() -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — 비용 0 판. 1910 의 허용 ①(엔진 체결 베낌) 없이 운영이 스스로 채운다.
    날마다 (가) 낸 회차(종목·지정가·수량)가 같고 (나) 찬 회차가 리플레이 잣대(그날 종가 ≤ 지정가)와 같고 (다) 체결 뒤 보유
    수량이 엔진(비중 × 자본 × 명목 ÷ 종가)과 정수 주 안에서, 현금 비중이 `ALLOW_REL` 안에서 같고 (라) 다음 판단 직전 장부의
    주당 원가(더 산 로트 포함)가 운영 `pp.positions` 의 cost ÷ quantity 와 같다. 헛돌지 않게: 2026-10-28 실측 낸 회차 696 ·
    찬 113 · 일부만 찬 종목-날 70 · 하나도 안 찬 156 · 찬 더 사기 61."""
    s, r, _재료, 장부 = _리플레이()
    live = _운영()
    prices = t10._자료()[1]
    로그 = {lg.day: lg for lg in s.log}
    자본 = dict(r.curve)
    N = cs.NOTIONAL_EQUITY
    틀림: list[str] = []
    낸수 = 찬수 = 일부 = 안참 = 더 = 견줌 = 0
    for k, day in enumerate(t10.모의[:-2]):
        h = live[day]
        lg = 로그[day]
        # 회차 비중은 판단 때(cutoff) 자본 대비 — 운영 원화 수량 = 비중 × 명목 × cutoff 자본(엔진 자본 1 = 입금 명목)
        cut = 자본[t10.모의[k - 1]] if k else 1.0
        리플낸 = {(sid, 가): w * N * cut / 가 for sid, 회차들 in lg.limits.items() for w, 가 in 회차들}
        운낸 = {(sid, 가): q for sid, 가, q in h["orders"]}
        if set(리플낸) != set(운낸) or any(abs(q - 운낸[x]) > ALLOW_SHARES for x, q in 리플낸.items()):
            틀림.append(f"{day} 낸 회차: 리플레이 {sorted(리플낸.items())} ≠ 운영 {sorted(운낸.items())}")
            continue
        리플찬 = {(sid, 가) for sid, 가 in 리플낸 if prices[sid][day] <= 가}
        운찬 = {(sid, 가) for sid, 가, _q in h["fills"]}
        if 리플찬 != 운찬:
            틀림.append(f"{day} 찬 회차: 리플레이 {sorted(리플찬)} ≠ 운영 {sorted(운찬)}")
        for sid in lg.limits:
            n = sum(1 for x in 리플찬 if x[0] == sid)
            낸수, 찬수 = 낸수 + len(lg.limits[sid]), 찬수 + n
            일부 += 0 < n < len(lg.limits[sid])
            안참 += n == 0
            더 += n > 0 and sid in lg.held
        reb = r.rebalances[k]
        엔진주식 = {sid: w * 자본[day] * N / prices[sid][day] for sid, w in reb.weights.items()}
        if set(엔진주식) != set(h["pos"]):
            틀림.append(f"{day} 보유: 엔진 {sorted(엔진주식)} ≠ 운영 {sorted(h['pos'])}")
            continue
        평가 = h["cash"] + sum(q * prices[sid][day] for sid, (q, _c) in h["pos"].items())
        if abs(h["cash"] / 평가 - (1 - sum(reb.weights.values()))) > ALLOW_REL:
            틀림.append(f"{day} 현금 비중: 엔진 {1 - sum(reb.weights.values())} ≠ 운영 {h['cash'] / 평가}")
        for sid, (q, 원가) in h["pos"].items():
            견줌 += 1
            if abs(엔진주식[sid] - q) > ALLOW_SHARES:
                틀림.append(f"{day} 종목 {sid}: 엔진 {엔진주식[sid]:.1f}주 ≠ 운영 {q}주")
            다음 = 장부[t10.모의[k + 1]].get(sid)
            if 다음 is None or abs(다음 / 원가 - 1) > ALLOW_REL:
                틀림.append(f"{day} 종목 {sid}: 원가 리플레이 {다음} ≠ 운영 {원가}")
    assert not 틀림, 틀림[:5]
    assert 낸수 >= 500 and 0 < 찬수 < 낸수 / 2 and 일부 >= 30 and 안참 >= 50 and 더 >= 30 and 견줌 >= 300, (
        낸수, 찬수, 일부, 안참, 더, 견줌)


# ── 실행기: `--limit-fills` 는 기본 꺼짐, 켜면 경고·params ──────────────────────────


@pytest.mark.parametrize("limit_fills", [False, True])
def test_run_core_는_기본_꺼짐이고_켜면_경고와_params_를_남긴다(monkeypatch: pytest.MonkeyPatch, limit_fills: bool) -> None:
    """25.1914 (전략 깔때기 ④ 분할 지정가 체결) — `run_core` 기본은 전략에 `limit_fills=False` 를 넘기고(예전과 같다)
    경고를 달지 않는다. `limit_fills=True`(명령줄 `--limit-fills`)면 전략이 켜지고 `WARN_LIMIT_FILLS` 경고·step_log
    params 에 남는다."""
    from batch.jobs import core_backtest as cj
    from tests import test_core_backtest_job_1534 as t34

    mem = t34._db(monkeypatch)
    받은: list[bool] = []
    원래 = cs.make_core_strategy

    def 엿봄(*a: object, **k: object) -> cs.CoreStrategy:
        s = 원래(*a, **k)  # type: ignore[arg-type]
        받은.append(s.limit_fills)
        return s

    monkeypatch.setattr(cs, "make_core_strategy", 엿봄)
    kw = {"limit_fills": True} if limit_fills else {}
    assert cj.run_core("KR", years=1, today=t34.오늘, force=True, variants=("core_b",), **kw) == 0
    assert 받은 == [limit_fills]
    (r,) = t34._행들(mem)
    assert (cj.WARN_LIMIT_FILLS in json.loads(r["warnings_json"])) is limit_fills
    # 회차 지정가 실행은 잡 이름을 가르고 짝 판정을 내지 않는다(25.1918) — 웹 핵심 H 카드가 기본 실행으로 읽지 않게
    잡 = cj.LIMIT_JOB_NAME if limit_fills else cj.JOB_NAME
    로그 = json.loads(mem.execute("SELECT step_log FROM batch_runs WHERE job_name = ?", [잡]).dicts()[0]["step_log"])
    assert 로그["params"]["limit_fills"] is limit_fills
    assert ("짝 판정 없음" in 로그["verdict"]) is limit_fills
