"""전략 깔때기 ⑤ — 가치(A)·모멘텀(C)의 매도·사기 전 점검을 시점 자료로 (docs/funnel.md 4장 ⑤, docs/infra.md 25.1934).

리플레이(`core_strategy`)는 운영 `jobs/paper.prebuy_sell_flags`·`jobs/sell_flags.enrich`·`sell_flags.evaluate` 를 **그대로**
부르고, 그 DB 읽기에 시점 창(`_PitClient`)이 cutoff 까지의 자료로만 답한다.

지키는 것
- 재무악화(영업이익 적자 전환): 접수일 ≤ cutoff 인 재무 스냅샷만 — 접수일이 cutoff 뒤인 스냅샷은 없는 것(미끼)
- 재무악화(종합 점수 20점 하락): 매수 때 신호 점수 → 그 주 종합 점수
- 점검(유니버스 제외): 그 주 첫 cutoff 의 시점 유니버스에서 빠지면
- 감성급락: 리플레이에서는 늘 "판정 못 함"(빈 답) — 무료 과거 뉴스가 없어 영구 차이. 지어내지 않는다
- 사면 바로 팔 후보는 사지 않고 운영과 같은 skip 줄
- 끝까지(진짜 판단기·`SharedDays`)·탐색 실행 칸
"""

from __future__ import annotations

import copy
from datetime import date
from functools import cache

import pytest

from batch.jobs import paper as jp
from batch.services import backtest as bt
from batch.services import core_backtest as cb
from batch.services import core_strategy as cs
from batch.services import paper as pp
from batch.services import sell_flags as sf
from tests import test_core_strategy_1532 as t

# ── 손으로 짠 판단기 — 신호 행·재무 스냅샷·주간 점수·시점 유니버스를 정해 준다 ─────────────────────────

#: 손 사례 변형 — 운영 A·C 의 매도 규칙을 그대로, 고르기만 신호 상위(손으로 정한 신호 행을 그대로 산다)
_손변형 = {"hand_a": cs.Variant("A", "value", "tranche", 5, "signal"),
          "hand_c": cs.Variant("C", "momentum", "tranche", 5, "signal"),
          "hand_b": cs.Variant("B", "core", "tranche", 5, "signal")}  # fmt: skip


class 점검시장(t.가짜시장):
    """`가짜시장` + 사기 전 점검 계좌가 읽는 시점 자료(재무 스냅샷·주간 종합 점수·시점 유니버스)."""

    factor_weights = cb.EQUAL_WEIGHTS

    def __init__(self, universe: list[dict], 신호: dict, snaps: dict, 점수: dict[str, dict[int, float]],
                 밖: dict[str, set[int]] | None) -> None:  # fmt: skip
        super().__init__(universe, 신호)
        self.snapshots, self.점수, self.밖 = snaps, 점수, 밖
        self.물은_점수: list[str] = []

    def week_totals(self, score_cutoff: str) -> dict[int, float | None]:
        self.물은_점수.append(score_cutoff)
        out = {int(u["stock_id"]): 80.0 for u in self.universe}  # `_행` 의 신호 점수와 같다 — 하락 없음
        for d in sorted(self.점수):
            if d <= score_cutoff:
                out.update(self.점수[d])
        return out

    def members(self, cutoff: str) -> set[int] | None:
        if self.밖 is None:
            return None
        빠짐: set[int] = set()
        for d in sorted(self.밖):
            if d <= cutoff:
                빠짐 = set(self.밖[d])
        return {int(u["stock_id"]) for u in self.universe} - 빠짐


def _스냅(fy: int, as_of: str, oi: float, receipt: str = "") -> dict:
    return {"as_of_date": as_of, "fiscal_year": fy, "receipt_no": receipt or f"{fy}-{as_of}", "consolidated": True,
            "values": {"operating_income": oi, "revenue": 1e12, "currency": "KRW", "period_days": 365}}  # fmt: skip


def _돌림(variant: str, 가격: dict[int, list[float]], 신호: dict[int, list[tuple]], *, snaps=None, 점수=None, 밖=None):  # type: ignore[no-untyped-def]  # noqa: E501
    """`test_core_strategy_1532._돌림` 과 같은 엔진·같은 날짜(2024-01-05 부터 영업일). 날짜 열쇠는 번호(날 k)로 준다:
    `snaps` = {종목: [(사업연도, 접수일 번호, 영업이익)]}, `점수` = {cutoff 번호: {종목: 종합 점수}}, `밖` = {cutoff 번호: {종목}}.
    접수일 번호는 날 번호 그대로 — 결정일 날[k+1] 의 cutoff 가 날[k] 다"""
    n = max(len(v) for v in 가격.values())
    날 = t._영업일("2024-01-05", n)
    prices = {sid: {날[k]: p for k, p in enumerate(v) if p is not None} for sid, v in 가격.items()}
    스냅 = {sid: [_스냅(2022, "2023-03-15", 100.0)] + [_스냅(fy, 날[k], oi) for fy, k, oi in rows]
           for sid, rows in (snaps or {}).items()}  # fmt: skip
    m = 점검시장(t._종목들(max(가격)), {날[k]: rows for k, rows in 신호.items()}, 스냅,
               {날[k]: v for k, v in (점수 or {}).items()}, None if 밖 is None else {날[k]: v for k, v in 밖.items()})
    mp = pytest.MonkeyPatch()
    try:
        for k, v in _손변형.items():
            mp.setitem(cs.EXPLORE_VARIANTS, k, v)
        s = cs.make_core_strategy(variant, m, sessions=날, costs=bt.Costs.zero())
    finally:
        mp.undo()

    def 판단(day: str, view: bt.PriceView) -> bt.LedgerOrders:
        o = s(day, view)
        if view.stocks():
            assert set(s.lots) == set(view.holding or ()), day
        return o

    bt.simulate(prices, 날, 날, 판단, bt.Costs.zero(), has_delisted=True)
    return s, 날, m


def _매도날(s: cs.CoreStrategy) -> dict[str, dict[int, list[str]]]:
    return {lg.day: lg.sells for lg in s.log if lg.sells}


# ── 재무악화 — 영업이익 적자 전환(시점) ────────────────────────────────────────────


@pytest.mark.parametrize("variant", ["hand_a", "hand_c"])
def test_적자_전환은_접수일이_cutoff_에_든_다음_결정일에_판다(variant: str) -> None:
    """날[1] 에 산 종목의 FY2023 적자(전년 흑자) 사업보고서가 날[4] 에 접수 — 결정일 날[5](cutoff 날[4])에 재무악화로 판다.
    날[2]~날[4] 결정(cutoff 날[1]~날[3])은 그 보고서를 모른다(접수일 > cutoff — 미래). 가격은 평평(손절·목표 없음)"""
    s, 날, _m = _돌림(variant, {1: [100.0] * 9}, {0: [(1, 10.0)]}, snaps={1: [(2023, 4, -50.0)]})
    assert _매도날(s) == {날[5]: {1: ["재무악화"]}}, _매도날(s)


@pytest.mark.parametrize("variant", ["hand_a", "hand_c"])
def test_흑자이거나_정정으로_흑자가_되면_팔지_않는다(variant: str) -> None:
    """적자 보고서가 접수됐다가(날[4]) 같은 사업연도 정정(날[6], 흑자)이 들어오면 정정 뒤로는 재무악화가 없다 —
    그 사이(cutoff 날[4]·날[5])에는 판다(늦게 접수된 것이 이긴다). 흑자 → 흑자는 처음부터 없다"""
    s, 날, _m = _돌림(variant, {1: [100.0] * 9}, {0: [(1, 10.0)]}, snaps={1: [(2023, 8, 120.0)]})
    assert _매도날(s) == {}
    # 적자 보고서 뒤 정정 — 판 날은 날[5] 하나(그 뒤 다시 사지 않는 신호라 보유가 없다)
    s, 날, _m = _돌림(variant, {1: [100.0] * 9}, {0: [(1, 10.0)]}, snaps={1: [(2023, 4, -50.0), (2023, 6, 80.0)]})
    assert _매도날(s) == {날[5]: {1: ["재무악화"]}}


def test_core_계좌는_재무악화를_따르지_않고_시점_창도_쓰지_않는다() -> None:
    """B(core)는 같은 자료에서 팔지 않는다 — 보유와 무관한 플래그를 따르지 않으니 예전 길(`_ViewClient`) 그대로."""
    s, _날, m = _돌림("hand_b", {1: [100.0] * 9}, {0: [(1, 10.0)]}, snaps={1: [(2023, 4, -50.0)]})
    assert _매도날(s) == {} and not s.prebuy and m.물은_점수 == []


# ── 사기 전 점검 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("variant", ["hand_a", "hand_c"])
def test_사면_바로_팔_후보는_사지_않는다_접수일이_cutoff_뒤면_산다(variant: str) -> None:
    """cutoff 날[0] 의 신호 둘 — 종목 1 은 적자 보고서가 날[0] 에 접수(cutoff 에 앎) → 사지 않고 운영과 같은 skip 줄,
    종목 2 는 날[1] 에 접수(cutoff 뒤 — 모름) → 날[1] 에 산다. 다음 결정(cutoff 날[1])에서 종목 2 를 재무악화로 판다"""
    s, 날, _m = _돌림(variant, {1: [100.0] * 5, 2: [100.0] * 5}, {0: [(1, 10.0), (2, 10.0)]},
                     snaps={1: [(2023, 0, -50.0)], 2: [(2023, 1, -50.0)]})  # fmt: skip
    로그 = {lg.day: lg for lg in s.log}
    assert 로그[날[1]].buys == pytest.approx({2: 0.10})
    skip = [d for d in 로그[날[1]].decisions if d["stock_id"] == 1]
    assert skip == [{"stock_id": 1, "kind": "skip", "reason": "사면 바로 매도 규칙에 걸림: 재무악화", "ref_close": 100.0,
                     "ref_date": 날[0], "detail": {"flags": ["재무악화"], "sell_rule": _손변형[variant].sell_rule,
                                                  "horizon": "short", "score": 80.0}}]  # fmt: skip
    assert _매도날(s) == {날[2]: {2: ["재무악화"]}}


def test_사기_전_점검은_운영_prebuy_sell_flags_를_부른다(monkeypatch: pytest.MonkeyPatch) -> None:
    """판단식을 다시 적지 않는다 — 운영 함수를 바꿔 끼우면 리플레이의 skip 이 따라 바뀐다."""
    불린: list[tuple] = []

    def 가짜(client, market, sell_rule, rows, held, frozen, *a):  # type: ignore[no-untyped-def]
        불린.append((market, sell_rule, sorted(r.stock_id for r in rows), sorted(held), sorted(frozen)))
        return {2: ["점검"]}

    monkeypatch.setattr(jp, "prebuy_sell_flags", 가짜)
    s, 날, _m = _돌림("hand_a", {1: [100.0] * 4, 2: [100.0] * 4}, {0: [(1, 10.0), (2, 10.0)]})
    assert 불린[0] == ("KR", "value", [1, 2], [], [])
    assert {lg.day: lg.buys for lg in s.log if lg.buys} == {날[1]: pytest.approx({1: 0.10})}


# ── 재무악화 — 종합 점수 20점 하락 · 점검 ─────────────────────────────────────────────


@pytest.mark.parametrize(("떨어진", "판다"), [(60.0, True), (60.1, False)])
def test_종합_점수_20점_하락(떨어진: float, 판다: bool) -> None:
    """매수 때 신호 점수 80 → cutoff 날[3] 주간 점수 60(정확히 −20) 이면 결정일 날[4] 에 재무악화, 60.1 이면 아니다.
    A(value) 는 재무악화를 따른다"""
    s, 날, _m = _돌림("hand_a", {1: [100.0] * 7}, {0: [(1, 10.0)]}, 점수={3: {1: 떨어진}})
    assert _매도날(s) == ({날[4]: {1: ["재무악화"]}} if 판다 else {})


@pytest.mark.parametrize("variant", ["hand_a", "hand_c"])
def test_시점_유니버스에서_빠지면_점검으로_판다(variant: str) -> None:
    """cutoff 날[3] 부터 종목 1 이 시점 유니버스 밖 → 결정일 날[4] 에 점검. 유니버스를 자르지 않는 실행(`members` None)은
    제외를 모른다 — 팔지 않는다"""
    s, 날, _m = _돌림(variant, {1: [100.0] * 7}, {0: [(1, 10.0)]}, 밖={3: {1}})
    assert _매도날(s) == {날[4]: {1: ["점검"]}}
    s, 날, _m = _돌림(variant, {1: [100.0] * 7}, {0: [(1, 10.0)]})
    assert _매도날(s) == {}


# ── 감성급락 — 영구 차이 ─────────────────────────────────────────────────────────


def test_감성은_늘_빈_답이라_감성급락은_판정하지_못한다() -> None:
    """C(momentum)는 감성급락을 따르지만 리플레이에는 무료 과거 뉴스·감성 이력이 없다 — 시점 창은 감성 읽기에 빈 답을 주고,
    보강된 보유는 감성 칸이 비어 `sf.unjudged` 가 감성급락을 "판정 못 함" 으로 센다(지어내지 않는다, backtest.md 10장)."""
    보강: list[sf.HoldingInput] = []
    원래 = cs.CoreStrategy._enrich

    def 엿봄(self, *a, **k):  # type: ignore[no-untyped-def]
        h = 원래(self, *a, **k)
        보강.append(h)
        return h

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(cs.CoreStrategy, "_enrich", 엿봄)
        s, 날, _m = _돌림("hand_c", {1: [100.0] * 6}, {0: [(1, 10.0)]})
    finally:
        mp.undo()
    assert 보강 and all(h.sentiment_delta_7d is None and h.sentiment_now is None and h.sentiment_date is None
                      for h in 보강)  # fmt: skip
    assert all("감성급락" in sf.unjudged(h, date.fromisoformat(날[2])) for h in 보강)
    c = cs._PitClient(bt.PriceView({1: {날[0]: 100.0}}, 날[0]), snapshots={}, totals={}, score_book={},
                      members=lambda _d: None, score_cutoff=날[0], snap_days=[날[0]], weights_json="{}")  # fmt: skip
    assert c.execute("SELECT sentiment, delta_7d, negative_count_7d, as_of_date FROM sentiment_scores WHERE stock_id = ?"
                     " ORDER BY as_of_date DESC LIMIT 1", [1]).dicts() == []  # fmt: skip


def test_시점_창은_모르는_읽기에_멈춘다() -> None:
    """운영 `enrich` 가 읽기를 더하거나 잣대가 바뀐 갈래(점수 이력)를 타면 조용히 빈 값을 주지 않고 깨진다."""
    from batch.jobs import sell_flags as sf_job

    c = cs._PitClient(bt.PriceView({1: {"2024-01-05": 100.0}}, "2024-01-05"), snapshots={}, totals={},
                      score_book={}, members=lambda _d: None, score_cutoff="2024-01-05", snap_days=[],
                      weights_json="{}")  # fmt: skip
    with pytest.raises(LookupError):
        c.execute(sf_job.CHAIN_HISTORY_SQL, [1, "a", "b"])
    assert c.misses


def test_시점_창은_운영_enrich_의_읽기마다_답한다() -> None:
    """운영 `enrich` 를 시점 창으로 그대로 돌려 읽기 머리가 모두 맞는지 — 머리 글이 운영 SQL 과 어긋나면 그 읽기가 "모르는
    읽기" 로 멈춘다. 유니버스 밖 종목으로 매수 뒤 편입 이력 읽기(`_UNI_SINCE_SQL_HEAD`)까지 지난다"""
    from batch.jobs import sell_flags as sf_job

    c = cs._PitClient(bt.PriceView({1: {"2024-01-05": 100.0}}, "2024-01-05"),
                      snapshots={1: [_스냅(2022, "2023-03-15", 100.0), _스냅(2023, "2024-01-05", -1.0)]},
                      totals={1: 50.0}, score_book={(1, "2024-01-02"): 80.0},
                      members=lambda d: {1} if d <= "2024-01-02" else set(), score_cutoff="2024-01-05",
                      snap_days=["2024-01-02", "2024-01-05"], weights_json="{}")  # fmt: skip
    읽음: list[str] = []
    원래 = c.execute

    def 기록(sql, params=()):  # type: ignore[no-untyped-def]
        읽음.append(sql)
        return 원래(sql, params)

    c.execute = 기록  # type: ignore[method-assign]
    행 = {"stock_id": 1, "name": "종목1", "country": "KR", "horizon": "short", "first_buy_date": "2024-01-03",
         "currency": "KRW", "cost": 100.0, "market_value": 100.0, "price_date": "2024-01-05", "score_now": 50.0,
         "score_now_date": "2024-01-05", "score_at_buy": 80.0, "score_at_buy_date": "2024-01-02",
         "horizon_since": "2024-01-03", "lots": [("2024-01-03", 1.0, 100.0)], "quantity": 1.0}  # fmt: skip
    (h,), _경고 = sf_job.enrich(c, [행], date(2024, 1, 8), dividends=False)
    for 머리 in (cs._FIN_SQL_HEAD, cs._UNI_SQL_HEAD, cs._UNI_SINCE_SQL_HEAD, cs._SENTI_SQL_HEAD, cs._SCORE_ROW_SQL_HEAD,
                cs._RIGHTS_SQL_HEAD):  # fmt: skip
        assert any(q.startswith(머리) for q in 읽음), 머리
    assert not c.misses
    assert (h.op_income_latest, h.op_income_prev, h.fin_report_date) == (-1.0, 100.0, "2024-01-05")
    assert (h.score_at_buy, h.score_now, h.universe_excluded_reason) == (80.0, 50.0, cs.PIT_EXCLUDED_REASON)
    assert h.universe_included_since_buy is True and h.rights_scale == 1.0 and not h.corporate_action_recent
    assert {f.reason_code for f in sf.evaluate(h, date(2024, 1, 8))} == {"재무악화", "점검"}


# ── 재무 시점 고르기 ────────────────────────────────────────────────────────────


def test_pit_fin_rows_는_접수일_cutoff_로_자르고_정정은_늦은_것() -> None:
    snaps = [_스냅(2022, "2023-03-15", 100.0), _스냅(2023, "2024-03-14", -50.0, "r1"),
             _스냅(2023, "2024-04-01", 30.0, "r2"), _스냅(2024, "2025-03-14", 10.0)]  # fmt: skip
    assert [(r["fiscal_year"], r["operating_income"]) for r in cb.pit_fin_rows(snaps, "2024-03-13")] == [(2022, 100.0)]
    assert [(r["fiscal_year"], r["operating_income"], r["report_date"]) for r in cb.pit_fin_rows(snaps, "2024-03-14")] \
        == [(2023, -50.0, "2024-03-14"), (2022, 100.0, "2023-03-15")]
    assert cb.pit_fin_rows(snaps, "2024-04-01")[0]["operating_income"] == 30.0
    assert [r["fiscal_year"] for r in cb.pit_fin_rows(snaps, "2030-01-01")] == [2024, 2023]  # 둘까지
    assert cb.pit_fin_rows([], "2024-01-01") == []


# ── 끝까지 — 진짜 판단기 ─────────────────────────────────────────────────────────

#: 끝까지 미끼 — C 가 2023-03-10 에 사서 06-12 기간초과로 판 종목 10 에 FY2022 정정(적자)을 이 날 접수시킨다
_정정_종목, _정정_접수 = 10, "2023-04-03"


def _정정_자료() -> tuple:
    universe, prices, turn, snaps, idx = t._자료()
    snaps = copy.deepcopy(snaps)
    원 = next(r for r in snaps[_정정_종목] if r["fiscal_year"] == 2022)
    snaps[_정정_종목].append({**원, "as_of_date": _정정_접수, "receipt_no": "정정", "values": {**원["values"],
                                                                                      "operating_income": -1e9}})
    return universe, prices, turn, snaps, idx


@cache
def _끝(variant: str, 정정: bool = False, shared: str | None = None) -> tuple:
    """진짜 판단기(`CoreMarket`)로 끝까지 — `shared` 를 주면 그 변형을 먼저 같은 `SharedDays` 로 돌린 뒤 읽개로."""
    universe, prices, turn, snaps, idx = _정정_자료() if 정정 else t._자료()
    if shared is None:
        s = cs.make_core_strategy(variant, cb.CoreMarket(universe, snaps, idx), sessions=t.날들, costs=bt.Costs())
        r = bt.simulate(prices, t.모의, t.모의, s, bt.Costs(), turnover=turn, has_delisted=True)
        return s, r
    나눔 = cb.SharedDays(cb.CoreMarket(universe, snaps, idx))
    out = []
    for v in (shared, variant):
        s = cs.make_core_strategy(v, 나눔.reader(), sessions=t.날들, costs=bt.Costs())
        out.append((s, bt.simulate(prices, t.모의, t.모의, s, bt.Costs(), turnover=turn, has_delisted=True)))
    return out[1]


@pytest.mark.parametrize("variant", ["core_a", "core_c"])
def test_탐색_변형_A·C_가_끝까지_돈다(variant: str) -> None:
    s, r = _끝(variant)
    v = cs.EXPLORE_VARIANTS[variant]
    assert s.prebuy and s.v.top_n == 5 and len(r.curve) == len(t.모의)
    assert sum(1 for lg in s.log if lg.buys) >= 1
    사유 = {c for lg in s.log for x in lg.sells.values() for c in x}
    assert 사유 <= set(pp.SELL_RULES[v.sell_rule]) | {"미룬 매도"} and "감성급락" not in 사유, 사유
    assert {d["reason"] for lg in s.log for d in lg.decisions if d["kind"] == "buy"} == {"위원회 가결"}


def test_끝까지_정정_적자를_접수_다음_결정일에_팔고_다시_사지_않는다() -> None:
    """C 가 2023-03-10 에 산 종목 10 — 그 FY2022 정정(적자 전환)을 2023-04-03 에 접수시키면 cutoff 가 04-03 인
    결정일(04-04)에 재무악화로 판다. 그 전 결정은 미끼가 없을 때와 비트까지 같다(접수 전 정정은 없는 것 — look-ahead 없음).
    그 뒤로는 종목 10 을 사지 않는다(사기 전 점검)"""
    기본, 기본r = _끝("core_c")
    s, r = _끝("core_c", True)
    assert any(lg.buys.get(_정정_종목) for lg in 기본.log if lg.cutoff < _정정_접수)
    판날 = [(lg.day, lg.cutoff, lg.sells[_정정_종목]) for lg in s.log if _정정_종목 in lg.sells]
    assert 판날 == [("2023-04-04", _정정_접수, ["재무악화"])], 판날
    앞 = [lg for lg in s.log if lg.cutoff < _정정_접수]
    기본앞 = [lg for lg in 기본.log if lg.cutoff < _정정_접수]
    assert [(lg.day, lg.buys, lg.sells, lg.decisions) for lg in 앞] == [
        (lg.day, lg.buys, lg.sells, lg.decisions) for lg in 기본앞]
    assert [x for d, x in r.curve if d <= _정정_접수] == [x for d, x in 기본r.curve if d <= _정정_접수]
    assert not any(lg.buys.get(_정정_종목) for lg in s.log if lg.cutoff >= _정정_접수)


def test_나눠_읽어도_같다() -> None:
    """`SharedDays` 읽개(하루 재료에서 종합 점수가 빠진다 — 주간 점수를 따로 든다)로 돌려도 판단기를 직접 쓴 것과 같다.
    다른 변형(D)이 먼저 그 주를 적은 경우도"""
    s0, r0 = _끝("core_c", True)
    s1, r1 = _끝("core_c", True, shared="core_d")
    assert r1.curve == r0.curve
    assert [(lg.day, lg.buys, lg.sells, lg.decisions) for lg in s1.log] == [
        (lg.day, lg.buys, lg.sells, lg.decisions) for lg in s0.log]


def test_주간_점수는_지금_주_것만_준다() -> None:
    universe, _p, _t, snaps, idx = t._자료()
    m = cb.CoreMarket(universe, snaps, idx)
    with pytest.raises(ValueError, match="주간 점수"):
        m.week_totals("2023-01-02")


def test_탐색_변형_칸은_운영_A·C_계좌와_같다() -> None:
    """A: 0070 에 'all' 로 만들고 0077 이 `value` 로(진입·top_n 은 0075 열 기본값 tranche·5). C: 0075 에 open·5 로 만들고
    0077 이 `momentum` 으로. 둘 다 헌장 계좌(위원회 — 운영 `run_step` 은 헌장이 있으면 `committee_step`)"""
    from pathlib import Path

    from batch.services import company as co

    헌장 = Path("migrations/0077_paper_charters.sql").read_text(encoding="utf-8")
    기본 = Path("migrations/0075_paper_more_accounts.sql").read_text(encoding="utf-8")
    for 이름, aid in (("core_a", "A"), ("core_c", "C")):
        v = cs.EXPLORE_VARIANTS[이름]
        assert f"sell_rule = '{v.sell_rule}', name = '모의 {aid}" in 헌장 and v.selection == "committee"
        assert co.CHARTERS[aid]["sell_rule"] == v.sell_rule
    assert "entry_rule TEXT NOT NULL DEFAULT 'tranche'" in 기본 and "top_n INTEGER NOT NULL DEFAULT 5" in 기본
    assert (cs.EXPLORE_VARIANTS["core_a"].entry, cs.EXPLORE_VARIANTS["core_a"].top_n) == ("tranche", 5)
    assert "('C', '모의 C (시가 일괄 매수)', 'core', 1, '2026-10-15T00:00:00+00:00', 'open', 5, 'signal')" in 기본
    assert (cs.EXPLORE_VARIANTS["core_c"].entry, cs.EXPLORE_VARIANTS["core_c"].top_n) == ("open", 5)
