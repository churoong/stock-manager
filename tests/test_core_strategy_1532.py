"""핵심 H 백테스트 조각 1c — `services/core_strategy` (docs/backtest.md 10장, docs/infra.md 25.1532).

지키는 것
- 손으로 셀 수 있는 장부 사례(가짜 판단기 + 진짜 엔진 `bt.simulate`): B 는 목표에서 팔고 G 는 들고 가다 추적 손절,
  단기 기간초과는 첫 매수 뒤 93일째, 손절, 판 뒤 쉼, 목표 비중까지만 더 사기, 종목 10%·섹터 30%·현금 모자람은 통째로
  건너뜀, 오늘 판 돈은 오늘 쓰지 않음, 원가에 수수료
- 무작위 대조의 씨앗이 운영 `pp.random_order(pool, "R1|KR|신호 종목")` 과 같다
- 진짜 판단기(`CoreMarket`)로 다섯 변형이 돌고 사고판다 · 장부가 날마다 엔진 보유와 같다 · 결정적이다
- **미끼**: cutoff 뒤 가격을 바꿔도 그 전 결정일의 장부 주문이 같다(양성 대조: cutoff 날을 바꾸면 달라진다)
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta
from functools import cache

import pytest

from batch.notify import report_sections as rs
from batch.services import backtest as bt
from batch.services import core_backtest as cb
from batch.services import core_strategy as cs
from batch.services import paper as pp
from batch.services import report_picks
from batch.services import sell_flags as sf


def _영업일(시작: str, n: int) -> list[str]:
    d, out = date.fromisoformat(시작), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


# ── 가짜 판단기 — 신호 행만 정해 준다(장부·크기·매도는 진짜) ─────────────────────────


def _행(sid: int, cutoff: str, close: float, weight: float = 10.0, horizon: str = "short", score: float = 80.0,
        sector: str | None = None) -> report_picks.SignalRow:  # fmt: skip
    return report_picks.SignalRow(
        stock_id=sid, ticker=f"{sid:06d}", name=f"종목{sid}", market="KOSPI", horizon=horizon, signal_type="buy",
        currency="KRW", buy_zone_low=close, buy_zone_high=close, suggested_weight_pct=weight, suggested_amount=None,
        size_reduction=1.0,
        tranche_plan=[{"step": i + 1, "ratio": r, "price": close, "amount": None} for i, r in enumerate((0.4, 0.3, 0.3))],
        as_of_date=cutoff, total_score=score, close=close, close_date=cutoff, sector=sector,
        criteria=[sf._criterion("시험", "시험 신호", "시험", "signals", cutoff)],
    )  # fmt: skip


class 가짜시장:
    """`CoreMarket` 의 모양 — `at` 이 그날 신호 행(H 는 그 행이 곧 합의 고름)을 준다."""

    country = "KR"
    max_weight_per_stock = 10.0
    targets = None

    def __init__(self, universe: list[dict], 신호: dict[str, list[tuple]]) -> None:
        self.universe, self.신호 = universe, 신호

    def _pool(self, cutoff: str) -> list[dict]:
        return self.universe

    def at(self, view: bt.PriceView, day: str) -> cb.DayView:
        rows = [_행(sid, view.cutoff, view.last_close(sid), *rest) for sid, *rest in self.신호.get(view.cutoff, [])]
        return cb.DayView(
            cutoff=view.cutoff, day=day, score_cutoff=view.cutoff, factor_scores={}, total_scores={}, inputs={},
            signals={}, rows=rows, regimes={}, regime_factors={}, population=[], agenda=[], approved={}, votes={},
            company_weights={}, company_stats={}, ranked=[], picks=[r.stock_id for r in rows], pick_rows=rows, b_top=[],
        )  # fmt: skip


def _종목들(n: int, sector=lambda i: f"업종{i}") -> list[dict]:
    return [{"stock_id": i, "ticker": f"{i:06d}", "name": f"종목{i}", "market": "KOSPI", "sector": sector(i),
             "currency": "KRW"} for i in range(1, n + 1)]  # fmt: skip


def _돌림(variant: str, 가격: dict[int, list[float]], 신호: dict[int, list[tuple]], *, 시작: str = "2024-01-05",
          costs: bt.Costs | None = None, universe: list[dict] | None = None):  # fmt: skip
    """가격은 종목마다 날 순서 목록(날 0 = 첫 날). 신호는 {cutoff 번호: [(종목, 비중, 기간, 점수, 업종)…]}."""
    n = max(len(v) for v in 가격.values())
    날 = _영업일(시작, n)
    prices = {sid: {날[k]: p for k, p in enumerate(v)} for sid, v in 가격.items()}
    costs = costs if costs is not None else bt.Costs.zero()
    m = 가짜시장(universe or _종목들(max(가격)), {날[k]: rows for k, rows in 신호.items()})
    s = cs.make_core_strategy(variant, m, sessions=날, costs=costs)
    r = bt.simulate(prices, 날, 날, s, costs, has_delisted=True)
    return s, r, 날


def _날별(s: cs.CoreStrategy) -> dict[str, cs.DayLog]:
    return {lg.day: lg for lg in s.log}


def _까닭(lg: cs.DayLog, sid: int) -> str | None:
    return next((d["reason"] for d in lg.decisions if d["stock_id"] == sid), None)


# ── 매도 ─────────────────────────────────────────────────────────────────────


def test_B_는_목표에서_판다() -> None:
    """day1 에 100 으로 10% 산다 → day4 종가 110(+10%) → day5 에 목표도달로 전량. 그 전에는 팔지 않는다."""
    s, r, 날 = _돌림("core_b", {1: [100, 100, 100, 105, 110, 110, 110]}, {0: [(1, 10.0)]})
    로그 = _날별(s)
    assert 로그[날[1]].buys == pytest.approx({1: 0.10})
    assert [d for d in 날 if d in 로그 and 로그[d].sells] == [날[5]]
    assert 로그[날[5]].sells == {1: ["목표도달"]}
    assert 1 not in r.rebalances[5].weights and 1 in r.rebalances[4].weights


def test_G_는_목표에_닿아도_들고_가다_고점_대비_손절폭에서_판다() -> None:
    """고점 120(목표 +10% 넘음) → 111.7(−6.92%)은 들고 → 111.6(정확히 −7%)에서 추적손절."""
    s, _r, 날 = _돌림("core_g", {1: [100, 100, 105, 110, 120, 115, 111.7, 111.6, 111.6, 111.6]}, {0: [(1, 10.0)]})
    로그 = _날별(s)
    assert [d for d in 날 if d in 로그 and 로그[d].sells] == [날[8]]
    assert 로그[날[8]].sells == {1: [pp.TRAIL_CODE]}
    # 같은 길에서 B 는 110 을 본 다음 날 판다 — G−B 짝이 재는 차이
    b, _r, _날 = _돌림("core_b", {1: [100, 100, 105, 110, 120, 115, 111.7, 111.6, 111.6, 111.6]}, {0: [(1, 10.0)]})
    assert [d for d, lg in _날별(b).items() if lg.sells] == [날[4]]


@pytest.mark.parametrize("variant", ["core_b", "core_g"])
def test_손절(variant: str) -> None:
    s, _r, 날 = _돌림(variant, {1: [100, 100, 100, 93, 93, 93]}, {0: [(1, 10.0)]})
    로그 = _날별(s)
    assert 로그[날[4]].sells == {1: ["손절"]}
    assert not any(lg.sells for d, lg in 로그.items() if d != 날[4])


def test_단기는_첫_매수_뒤_93일째_기간초과() -> None:
    """2024-01-08(월) 첫 매수 → 92일째(04-09)는 들고 93일째(04-10)에 판다. 같은 날 산 중기 보유는 그대로(365일)."""
    n = 75
    s, _r, 날 = _돌림("core_b", {1: [100.0] * n, 2: [100.0] * n}, {0: [(1, 10.0, "short"), (2, 10.0, "mid")]})
    assert 날[1] == "2024-01-08"
    로그 = _날별(s)
    assert {d: lg.sells for d, lg in 로그.items() if lg.sells} == {"2024-04-10": {1: ["기간초과"]}}
    assert (date(2024, 4, 10) - date(2024, 1, 8)).days == sf.MAX_HOLDING_DAYS["short"] + 1


def test_원가에_수수료가_든다() -> None:
    """수수료 0.015% 면 원가 100.015 — 종가 110 은 +9.9985% 라 목표 아래, 110.02 에서 판다."""
    c = bt.Costs()
    s, _r, 날 = _돌림("core_b", {1: [100, 100, 110, 110, 110.02, 110.02, 110.02]}, {0: [(1, 10.0)]}, costs=c)
    assert s.unit_factor == pytest.approx(1 + c.commission_pct / 100)  # 지정가 매수 — 슬리피지 없음(운영 make_fill)
    assert {d: lg.sells for d, lg in _날별(s).items() if lg.sells} == {날[5]: {1: ["목표도달"]}}


# ── 판 뒤 쉼 ────────────────────────────────────────────────────────────────


def test_판_뒤_쉼() -> None:
    """day4 에 손절로 판 종목은 신호가 날마다 있어도 day4~day9 에 다시 사지 않고 day10 에 산다.

    운영 `쉼_시작` 은 리포트 거래일(= cutoff)에서 4 거래일 앞 — 체결일이 그 뒤인 매도와 오늘 매도가 막는다.
    결정일로 세면 판 날(s) 부터 s+5 까지 막히고 s+6 에 산다(판 뒤 `REBUY_COOLDOWN_SESSIONS` 거래일)."""
    가격 = [100, 100, 100, 93] + [93] * 10
    s, _r, 날 = _돌림("core_b", {1: 가격}, {k: [(1, 10.0)] for k in range(len(가격))})
    로그 = _날별(s)
    assert 로그[날[4]].sells == {1: ["손절"]}
    산날 = [d for d, lg in 로그.items() if 1 in lg.buys]
    assert 산날[:2] == [날[1], 날[4 + pp.REBUY_COOLDOWN_SESSIONS + 1]]
    for k in range(5, 10):
        assert _까닭(로그[날[k]], 1) == f"판 뒤 {pp.REBUY_COOLDOWN_SESSIONS}거래일 안"


# ── 크기 ─────────────────────────────────────────────────────────────────────


def test_보유는_목표_비중까지만_더_산다() -> None:
    """8% 신호. 100 에 8% → 95 로 내리면 비중 0.076/0.996 → 8% 까지의 차만 산다. 그다음 날·오른 날은 사지 않는다."""
    가격 = [100, 100, 95, 95, 95, 105, 105, 105]
    s, _r, 날 = _돌림("core_b", {1: 가격}, {k: [(1, 8.0)] for k in range(len(가격))})
    로그 = _날별(s)
    h = 0.08 * 0.95 / (0.08 * 0.95 + 0.92)
    assert {d: lg.buys for d, lg in 로그.items() if lg.buys} == {날[1]: pytest.approx({1: 0.08}),
                                                               날[3]: pytest.approx({1: 0.08 - h}, abs=1e-9)}
    assert _까닭(로그[날[2]], 1) == rs.EXCLUDED_STOCK_CAP and _까닭(로그[날[6]], 1) == rs.EXCLUDED_STOCK_CAP
    # 평균 원가 — 주식 수 비율은 산 날 종가로 잰 비중에서(옛 h/95 주, 새 (0.08 − h)/95 주)
    옛, 새 = h / 95, (0.08 - h) / 95
    assert s.lots[1].unit_cost == pytest.approx((옛 * 100 + 새 * 95) / (옛 + 새))
    assert s.lots[1].first_buy == 날[1]


def test_종목_10퍼센트_상한() -> None:
    s, _r, 날 = _돌림("core_b", {1: [100] * 4}, {0: [(1, 15.0)]})
    assert _날별(s)[날[1]].buys == pytest.approx({1: 0.10})


def test_섹터_30퍼센트_상한() -> None:
    """같은 업종 넷이 10% 씩 — 점수 순 셋만 사고 넷째는 섹터 상한으로 뺀다."""
    신호 = {0: [(i, 10.0, "short", 90.0 - i, "반도체") for i in range(1, 5)]}
    s, _r, 날 = _돌림("core_b", {i: [100] * 4 for i in range(1, 5)}, 신호, universe=_종목들(4, lambda i: "반도체"))
    lg = _날별(s)[날[1]]
    assert lg.buys == pytest.approx({1: 0.1, 2: 0.1, 3: 0.1})
    assert _까닭(lg, 4) == rs.EXCLUDED_SECTOR_CAP


def _현금_모자람():
    """day1·day2 에 9.5% 씩 열 종목(현금 5%) → day3 신호: 11(9.5%, 점수 높음)·12(4%) → day3 종가 1번 93 → day4 손절,
    그날 신호 13(9.5%)."""
    가격 = {i: [100.0] * 7 for i in range(1, 14)}
    가격[1] = [100, 100, 100, 93, 93, 93, 93]
    신호 = {0: [(i, 9.5, "short", 90.0) for i in range(1, 6)], 1: [(i, 9.5, "short", 90.0) for i in range(6, 11)],
            2: [(11, 9.5, "short", 90.0), (12, 4.0, "short", 80.0)], 3: [(13, 9.5, "short", 90.0)],
            4: [(13, 9.5, "short", 90.0)]}  # fmt: skip
    return _돌림("core_b", 가격, 신호)


def test_현금이_모자라면_통째로_건너뛴다() -> None:
    s, _r, 날 = _현금_모자람()
    lg = _날별(s)[날[3]]
    assert lg.buys == pytest.approx({12: 0.04})  # 11 은 일부도 사지 않는다
    assert _까닭(lg, 11) == rs.EXCLUDED_NO_BUDGET


def test_오늘_판_돈은_오늘_사는_데_쓰지_않는다() -> None:
    """day4 에 1번(9.3%)을 팔아도 그날 13(9.5%)은 cutoff 현금(약 1%)으로 보아 건너뛴다 — 다음 날 산다.
    엔진은 그날 판 몫만큼 자리가 있어 사 줄 수 있었다(운영은 시가 매도 체결 전 아침에 주문을 낸다)."""
    s, r, 날 = _현금_모자람()
    로그 = _날별(s)
    assert 로그[날[4]].sells == {1: ["손절"]}
    assert 13 not in 로그[날[4]].buys and _까닭(로그[날[4]], 13) == rs.EXCLUDED_NO_BUDGET
    assert 로그[날[5]].buys == pytest.approx({13: 0.095})
    # 엔진 자리: day4 매도 뒤 현금은 9.5% 를 넘었다 — 막은 것은 전략의 규칙이다
    assert 1 - sum(r.rebalances[4].weights.values()) > 0.095


# ── 무작위 대조 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("variant", ["core_r1", "core_r2"])
def test_무작위_씨앗은_운영과_같다(variant: str) -> None:
    """신호 종목 1 의 자리는 `pp.random_order(모집단, "R1|KR|1")` 의 첫 후보 — 운영 `random_substitute` 를 그대로 부른다.
    다음 날은 든 대체 종목을 잇는다(새 종목을 사지 않는다). 시가 일괄이라 원가에 슬리피지가 든다."""
    가격 = {i: [100.0 + i] * 5 for i in range(1, 31)}
    c = bt.Costs()
    s, _r, 날 = _돌림(variant, 가격, {0: [(1, 10.0)], 1: [(1, 10.0)]}, costs=c)
    계좌 = cs.VARIANTS[variant].account
    고른 = pp.random_order(list(range(1, 31)), f"{계좌}|KR|1")[0]
    로그 = _날별(s)
    assert 로그[날[1]].random["random"] == {"1": 고른}
    assert set(로그[날[1]].buys) == {고른} and not 로그[날[2]].buys
    assert 로그[날[2]].random["random"] == {"1": 고른}
    assert set(s.lots) == {고른} and s.lots[고른].substitute_of == 1
    assert s.unit_factor == pytest.approx(1 + (c.commission_pct + c.slippage_pct) / 100)


def test_R1_과_R2_는_다른_씨앗() -> None:
    pool = list(range(1, 31))
    assert pp.random_order(pool, "R1|KR|1")[0] != pp.random_order(pool, "R2|KR|1")[0]


def test_변형_칸은_운영_계좌와_같다() -> None:
    """마이그레이션 0070(B)·0075(R1·R2)·0082(G)·0083(H) 의 sell_rule·entry_rule·top_n·selection."""
    from pathlib import Path

    글 = "".join(p.read_text(encoding="utf-8") for p in sorted(Path("migrations").glob("00[78]*_paper*.sql")))
    for v in cs.VARIANTS.values():
        assert v.sell_rule in pp.SELL_RULES
        if v.account == "B":
            assert "('B', '모의 B (손절·목표·기간만)', 'core'" in 글 and (v.entry, v.top_n, v.selection) == (
                "tranche", 5, "signal")  # 0075 기본값
        else:
            assert f"'{v.sell_rule}', 1, '2026-10-1" in 글.split(f"('{v.account}', ")[1][:120]
            줄 = 글.split(f"('{v.account}', ")[1].split("\n")[0]
            assert f"'{v.entry}', {v.top_n}, '{v.selection}'" in 줄, 줄
    with pytest.raises(ValueError):
        cs.make_core_strategy("core_x", 가짜시장([], {}), sessions=[])


# ── 진짜 판단기로 끝까지 ─────────────────────────────────────────────────────

W, S, N = 130, 300, 12
전체 = _영업일("2022-01-03", W + S + 10)
날들 = 전체[: W + S]
모의 = 날들[W:]


@cache
def _자료() -> tuple:
    rng = random.Random(11)
    universe, prices, turn, snaps = [], {}, {}, {}
    for i in range(1, N + 1):
        universe.append({"stock_id": i, "ticker": f"{i:06d}", "name": f"종목{i}", "market": "KOSPI" if i % 3 else "KOSDAQ",
                         "sector": ["반도체", "은행", "화학", "유통"][i % 4], "currency": "KRW", "listed_shares": 10_000_000})  # fmt: skip
        p, drift, vol = 10_000.0, rng.uniform(-0.0005, 0.0015), rng.uniform(0.01, 0.03)
        prices[i], turn[i] = {}, {}
        for k, d in enumerate(날들):
            p *= math.exp(drift + vol * rng.gauss(0, 1))
            prices[i][d] = round(p, 2)
            turn[i][d] = 1e9 * rng.uniform(0.8, 1.2) * (1.7 if (k + 7 * i) // 30 % 2 == 0 else 1.0)
        rev, g, 마진 = rng.uniform(1e11, 1e12), rng.uniform(-0.1, 0.4), rng.uniform(0.05, 0.15)
        snaps[i] = []
        for fy in range(2017, 2023):
            rev *= 1 + g + rng.uniform(-0.05, 0.05)
            oi = rev * 마진
            snaps[i].append({"as_of_date": f"{fy + 1}-03-15", "fiscal_year": fy, "receipt_no": f"{fy}{i:04d}",
                             "consolidated": True, "values": {
                                 "revenue": rev, "operating_income": oi, "net_income": oi * 0.7,
                                 "total_equity": rev * rng.uniform(0.3, 1.5), "total_assets": rev * 2,
                                 "total_liabilities": rev * 0.8, "currency": "KRW"}})  # fmt: skip
    idx: dict[str, dict[str, float]] = {"KOSPI": {}, "KOSDAQ": {}}
    a, b = 2500.0, 800.0
    for d in 날들:
        a *= math.exp(0.0003 + 0.01 * rng.gauss(0, 1))
        b *= math.exp(0.0002 + 0.012 * rng.gauss(0, 1))
        idx["KOSPI"][d], idx["KOSDAQ"][d] = a, b
    return universe, prices, turn, snaps, idx


def _끝까지(variant: str, prices: dict | None = None) -> tuple[cs.CoreStrategy, bt.BacktestResult, dict]:
    """엔진으로 돌리며 날마다 ① 장부와 엔진 보유가 같은지 보고 ② 장부 주문을 남긴다."""
    universe, p0, turn, snaps, idx = _자료()
    prices = prices or p0
    s = cs.make_core_strategy(variant, cb.CoreMarket(universe, snaps, idx), sessions=날들, costs=bt.Costs())
    주문: dict[str, bt.LedgerOrders] = {}

    def 판단(day: str, view: bt.PriceView) -> bt.LedgerOrders:
        o = s(day, view)
        assert set(s.lots) == set(view.holding or ()), f"{day}: 전략 장부와 엔진 보유가 다르다"
        주문[day] = o
        return o

    r = bt.simulate(prices, 모의, 모의, 판단, bt.Costs(), turnover=turn, has_delisted=True)
    return s, r, 주문


@cache
def _기본(variant: str) -> tuple:
    return _끝까지(variant)


@pytest.mark.parametrize("variant", sorted(cs.VARIANTS))
def test_다섯_변형이_돌고_사고판다(variant: str) -> None:
    s, r, 주문 = _기본(variant)
    v = cs.VARIANTS[variant]
    assert len(r.curve) == len(모의) and all(x > 0 and math.isfinite(x) for _d, x in r.curve)
    assert sum(len(o.buys) for o in 주문.values()) >= 5 and sum(len(o.sells) for o in 주문.values()) >= 2
    사유 = {c for lg in s.log for cs_ in lg.sells.values() for c in cs_}
    assert 사유 <= set(pp.SELL_RULES[v.sell_rule]) | {"미룬 매도"}, 사유
    # 사는 날 한 종목 비중이 상한(10%)을 넘지 않는다(엔진 비용·반올림 여유)
    for lg in s.log:
        assert all(0 < x <= 0.1 + 1e-6 for x in lg.buys.values()), lg.buys
    if v.selection == "weighted":
        # H 의 결정은 판단기 원장에 쌓이고 성과가 차 회사 가중치를 움직인다
        assert any(row.kind == "pick" for row in s.market.ledger.rows)


def test_G_와_B_는_매도만_다르다() -> None:
    g, b = _기본("core_g")[0], _기본("core_b")[0]
    assert not any("목표도달" in c for lg in g.log for c in lg.sells.values())
    assert not any(pp.TRAIL_CODE in c for lg in b.log for c in lg.sells.values())
    # 첫 매수 날까지는 같은 것을 산다(같은 신호·같은 점수 상위 5)
    첫 = next(lg for lg in b.log if lg.buys)
    assert _날별(g)[첫.day].buys == pytest.approx(첫.buys)


def test_결정적이다() -> None:
    s1, r1, o1 = _기본("core_r1")
    s2, r2, o2 = _끝까지("core_r1")
    assert r1.curve == r2.curve and o1 == o2 and s1.lots == s2.lots


def _미끼(부터: str) -> dict:
    """`부터` 날(포함) 뒤 가격을 마구 바꾼다 — 홀수 종목은 세 배, 짝수 종목은 5분의 1."""
    _u, p0, *_ = _자료()
    return {sid: {d: (c * (3 if sid % 2 else 0.2) if d >= 부터 else c) for d, c in by.items()} for sid, by in p0.items()}


@pytest.mark.parametrize("variant", ["core_h", "core_g"])
def test_미끼_cutoff_뒤_가격은_그_전_주문을_바꾸지_않는다(variant: str) -> None:
    """X 뒤 가격을 바꿔도 cutoff ≤ X 인 결정일(X 다음 날까지)의 장부 주문이 같다. 양성 대조: X 날부터 바꾸면
    X 다음 날 주문이 달라진다(든 종목이 세 배·5분의 1 이 되어 목표·손절에 걸린다)."""
    _s, _r, 기본 = _기본(variant)
    k = len(모의) - 40
    X = 모의[k]
    assert any(기본[d].buys or 기본[d].sells for d in 모의[k - 30 : k + 2])
    _s2, _r2, 미끼 = _끝까지(variant, _미끼(모의[k + 1]))
    for d in 모의[: k + 2]:
        assert 미끼[d] == 기본[d], f"{d}: cutoff 뒤 가격이 주문을 바꿨다 — look-ahead"
    _s3, _r3, 대조 = _끝까지(variant, _미끼(X))
    assert all(대조[d] == 기본[d] for d in 모의[: k + 1])
    assert 대조[모의[k + 1]] != 기본[모의[k + 1]], "양성 대조: cutoff 날 가격을 바꿨는데 주문이 같다 — 미끼가 물릴 자리가 없다"
