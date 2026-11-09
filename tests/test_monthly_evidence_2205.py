"""월 판정판 기록 칸 — 꺼진 조각 (docs/factors.md 12.45 "u2 판정판 기록 칸 + core_m 사전 등록" (a)·(b)·(c)).

(a) 월 엔진 `rebalances` → 기록 꼴 → `ce.evidence` 그대로(실행 시장 씨앗, 묶음 최소 1일, 첫 매수만, 20거래일, 실패 표지)
(b) `split_half` services 로 옮김 — 옛 식과 비트까지 같다
(c) 코호트 BH m = max(N 최댓값, 줄 수) · N 없으면 None
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

import pytest

from batch.jobs import core_backtest as cj
from batch.services import backtest as bt
from batch.services import core_evidence as ce
from batch.services import metrics as m
from batch.services import monthly_evidence as me
from batch.services import paper as pp


def _평일(n: int, 시작: str = "2025-01-01") -> list[str]:
    d, out = date.fromisoformat(시작), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


#: 가격 축 260 평일 — 엔진 날짜 축은 앞 30일(워밍업)을 뺀 나머지
가격날 = _평일(260)
엔진날 = 가격날[30:]
N_STOCK = 40


def _흔들(sid: int, k: int) -> float:
    h = hashlib.sha256(f"{sid}|{k}".encode()).digest()
    return (h[0] - 127.5) / 127.5 * 0.02  # ±2%


def _자료() -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    for sid in range(1, N_STOCK + 1):
        v, row = 100.0, {}
        for k, d in enumerate(가격날):
            v *= 1 + _흔들(sid, k)
            row[d] = v
        out[sid] = row
    return out


PRICES = _자료()
INDEX = {d: 1000.0 * (1 + 0.0002 * k) for k, d in enumerate(가격날)}


def _월초(dates: list[str]) -> list[str]:
    return [d for i, d in enumerate(dates) if i == 0 or d[:7] != dates[i - 1][:7]]


def _리밸(d: str, ws: dict[int, float], dropped: list[int] | None = None) -> bt.Rebalance:
    return bt.Rebalance(date=d, weights=ws, turnover=0.0, cost=0.0, dropped=dropped or [])


def _돌림(k: int) -> list[bt.Rebalance]:
    """월마다 다섯 종목 — 둘은 이어 들고(하나는 비중만 바꿈) 셋은 새로. 0 비중 하나 섞음"""
    out = []
    for i, d in enumerate(_월초(엔진날)):
        새 = [(i * 3 + j) % N_STOCK + 1 for j in range(k)]
        ws = {s: 1 / k for s in 새}
        if out:
            앞 = sorted(s for s, w in out[-1].weights.items() if w > 0)
            ws[앞[0]] = 0.3  # 이어 들며 비중만 바꿈
            ws[앞[1]] = 1 / k
        ws[N_STOCK] = 0.0  # 0 비중 — 산 것 아님
        out.append(_리밸(d, ws))
    return out


def _모집단(_ref: str) -> set[int]:
    return set(range(1, N_STOCK + 1))


# ── (a) 기록 꼴 ─────────────────────────────────────────────────────────────


def test_기록_꼴_day_cutoff_held_buys() -> None:
    a, b, c = _월초(엔진날)[:3]
    rs = [_리밸(a, {1: 0.5, 2: 0.5, 9: 0.0}), _리밸(b, {1: 0.7, 3: 0.3}), _리밸(c, {3: 0.2, 4: 0.4, 5: 0.4}, dropped=[6])]
    logs = me.month_logs(rs, 엔진날, PRICES)
    assert [x.day for x in logs] == [a, b, c]
    assert [x.cutoff for x in logs] == [bt.decision_cutoff(엔진날, PRICES, t) for t in (a, b, c)]
    # 첫 리밸런스 cutoff 는 워밍업 가격의 마지막 날(엔진 날짜 축 밖)
    assert logs[0].cutoff == 가격날[29]
    assert logs[0].held == frozenset() and logs[0].buys == (1, 2)  # 0 비중(9)은 산 것 아님
    assert logs[1].held == frozenset({1, 2}) and logs[1].buys == (3,)  # 1 은 비중만 바꿈 — 새 매수 아님
    assert logs[2].held == frozenset({1, 3}) and logs[2].buys == (4, 5)  # 3 은 이어 듦


def test_체결가_없어_뺀_종목은_사지_않은_것() -> None:
    a = _월초(엔진날)[0]
    logs = me.month_logs([_리밸(a, {1: 0.5, 2: 0.5}, dropped=[2])], 엔진날, PRICES)
    assert logs[0].buys == (1,)


def test_ce_first_buys_와_같다_첫_매수만() -> None:
    rs = _돌림(5)
    logs = me.month_logs(rs, 엔진날, PRICES)
    산 = ce.first_buys(logs)
    out = me.evidence(rs, 엔진날, PRICES, "KR", _모집단, INDEX)
    assert "evidence_error" not in out
    assert out["evidence"]["buys"] == len(산) > 0
    # 손으로: 이어 든 종목·비중만 바꾼 종목·0 비중은 빠지고, 28일 안 되풀이도 빠진다
    기대: list[tuple[str, int]] = []
    마지막: dict[int, date] = {}
    앞: set[int] = set()
    for r in rs:
        지금 = {s for s, w in r.weights.items() if w > 0}
        cut = bt.decision_cutoff(엔진날, PRICES, r.date)
        for s in sorted(지금 - 앞):
            d = date.fromisoformat(r.date)
            if s in 마지막 and (d - 마지막[s]).days < pp.ATTRIBUTION_EPISODE_DAYS:
                continue
            마지막[s] = d
            기대.append((cut, s))
        앞 = 지금
    assert 산 == 기대
    assert all(sid != N_STOCK for _c, sid in 산)  # 0 비중 종목은 한 번도 안 셈


def test_ce_evidence_를_그대로_부른다() -> None:
    rs = _돌림(5)
    logs = me.month_logs(rs, 엔진날, PRICES)
    for 시장 in ("KR", "US"):
        e = me.evidence(rs, 엔진날, PRICES, 시장, _모집단, INDEX)["evidence"]
        assert e == ce.evidence(logs, ce.Closes(PRICES), _모집단, INDEX, market=시장) | {"version": 1}
        assert e["version"] == me.EVIDENCE_VERSION == 1


def test_씨앗과_대조가_운영_control_picks_와_같다(monkeypatch: pytest.MonkeyPatch) -> None:
    """미국 실행의 씨앗은 "virtual|US|ref" — `core_evidence` 기본값 "KR" 이 아니다(12.45 (a))"""
    씨앗: list[str] = []
    원래_순서 = pp.random_order
    부름: list[tuple[str, str, int, list[int]]] = []
    원래_대조 = pp.control_picks

    def 순서(pool: list[int], seed: str) -> list[int]:
        씨앗.append(seed)
        return 원래_순서(pool, seed)

    def 대조(universe: list[int], market: str, ref: str, exclude: int) -> list[int]:
        got = 원래_대조(universe, market, ref, exclude)
        부름.append((market, ref, exclude, got))
        return got

    monkeypatch.setattr(pp, "random_order", 순서)
    monkeypatch.setattr(pp, "control_picks", 대조)
    out = me.evidence(_돌림(5), 엔진날, PRICES, "US", _모집단, INDEX)["evidence"]
    assert out["scored"] > 0 and 부름
    assert 씨앗 and all(s.startswith("virtual|US|") for s in 씨앗)
    for market, ref, exclude, got in 부름:
        assert market == "US"
        # 운영 fill_controls 와 같은 식(그날 시점 유니버스 정렬, 실행 시장 씨앗, 산 종목 뺌, 20개)
        assert got == [c for c in 원래_순서(sorted(_모집단(ref)), f"virtual|US|{ref}")[: pp.VIRTUAL_CONTROLS + 1]
                       if c != exclude][: pp.VIRTUAL_CONTROLS]  # fmt: skip
        assert len(got) == pp.VIRTUAL_CONTROLS and exclude not in got


def test_미국은_KR_씨앗과_다른_대조를_쓴다() -> None:
    """같은 자료라도 시장 씨앗이 다르면 대조가 달라 분위 묶음이 갈린다 — 기본값 "KR" 이 새면 이 시험이 깨진다"""
    rs = _돌림(5)
    us = me.evidence(rs, 엔진날, PRICES, "US", _모집단, INDEX)["evidence"]
    kr = me.evidence(rs, 엔진날, PRICES, "KR", _모집단, INDEX)["evidence"]
    assert us["blocks"] != kr["blocks"]


def test_묶음_최소_날_수는_1() -> None:
    assert ce.CORE_BLOCK_MIN_DAYS == 1
    rs = _돌림(5)
    e = me.evidence(rs, 엔진날, PRICES, "KR", _모집단, INDEX)["evidence"]
    # 월 1회 리밸런스 — 28일 묶음마다 매수 날은 많아야 이틀. 점수 난 날마다 묶음 하나가 선다(1일 묶음도 셈)
    점수날 = [c for c, s in ce.first_buys(me.month_logs(rs, 엔진날, PRICES))
             if ce.Closes(PRICES).forward(s, c, pp.ATTRIBUTION_HORIZON) is not None]  # fmt: skip
    assert len(e["blocks"]) == len({pp._묶음(d) for d in 점수날}) == e["n_blocks"]
    assert len(e["blocks"]) >= pp.EVIDENCE_MIN_BLOCKS and e["lower"] is not None


def test_수익_창은_20거래일() -> None:
    """산 종목은 +20일에만 10% 오르고, 대조는 +19일에 두 배였다가 +20일에 제자리 — 20일 창이면 분위 100,
    19일 창이었다면 0"""
    날 = _평일(80)
    ref_k = 10
    prices: dict[int, dict[str, float]] = {}
    for sid in range(1, 31):
        row = {d: 100.0 for d in 날}
        if sid == 1:
            row[날[ref_k + 20]] = 110.0
        else:
            row[날[ref_k + 19]] = 200.0
        prices[sid] = row
    t = 날[ref_k + 1]
    out = me.evidence([_리밸(t, {1: 1.0})], 날, prices, "KR", lambda _r: set(prices), {d: 1.0 for d in 날})
    e = out["evidence"]
    assert pp.ATTRIBUTION_HORIZON == 20
    assert (e["buys"], e["scored"]) == (1, 1)
    assert e["blocks"] == {str(pp._묶음(날[ref_k])): 100.0}  # cutoff = t-1 = 날[ref_k]


def test_끝_20일_안_매수는_점수를_내지_않는다() -> None:
    rs = [_리밸(엔진날[-5], {1: 1.0})]
    e = me.evidence(rs, 엔진날, PRICES, "KR", _모집단, INDEX)["evidence"]
    assert (e["buys"], e["scored"], e["n_blocks"]) == (1, 0, 0)


def test_실패하면_evidence_error_던지지_않는다() -> None:
    rs = _돌림(5)
    out = me.evidence(rs, 엔진날, PRICES, "JP", _모집단, INDEX)
    assert set(out) == {"evidence_error"} and out["evidence_error"].startswith("ValueError: 실행 시장은 KR·US")

    def 깨짐(_ref: str) -> set[int]:
        raise RuntimeError("유니버스 없음")

    out = me.evidence(rs, 엔진날, PRICES, "US", 깨짐, INDEX)
    assert out == {"evidence_error": "RuntimeError: 유니버스 없음"}
    out = me.evidence([object()], 엔진날, PRICES, "KR", _모집단, INDEX)  # 꼴이 깨진 리밸런스
    assert set(out) == {"evidence_error"} and out["evidence_error"].startswith("AttributeError")


def test_빈_실행은_빈_증거() -> None:
    e = me.evidence([], 엔진날, PRICES, "KR", _모집단, INDEX)["evidence"]
    assert (e["buys"], e["scored"], e["n_blocks"], e["version"]) == (0, 0, 0, 1)


# ── (b) split_half — 옮김, 비트까지 같음 ──────────────────────────────────────


def _옛_split_half(curve: list[tuple[str, float]]) -> dict[str, Any]:
    """옮기기 전 `jobs/core_backtest.split_half` 본문을 글자 그대로 — 옮긴 식이 바뀌면 이 시험이 깨진다"""
    if len(curve) < 3:
        return {}
    가운데 = len(curve) // 2
    out: dict[str, Any] = {}
    for 이름, 조각 in (("first", curve[: 가운데 + 1]), ("second", curve[가운데:])):
        점 = [m.PricePoint(date=date.fromisoformat(d), close=v) for d, v in 조각]
        out[이름] = {"start": 조각[0][0], "end": 조각[-1][0], "days": len(조각),
                    "cagr": m.cagr(점), "mdd": m.max_drawdown(점).mdd}  # fmt: skip
    return out


def _곡선(n: int, seed: int) -> list[tuple[str, float]]:
    날 = _평일(n, "2019-03-01")
    v, out = 1.0, []
    for k, d in enumerate(날):
        v *= 1 + _흔들(seed, k)
        out.append((d, v))
    return out


@pytest.mark.parametrize("n", [0, 1, 2, 3, 4, 5, 10, 11, 252, 253, 777])
def test_split_half_옛_식과_비트까지_같다(n: int) -> None:
    c = _곡선(n, 1000 + n)
    새, 옛 = me.split_half(c), _옛_split_half(c)
    assert 새 == 옛  # float == — approx 아님
    assert repr(새) == repr(옛)
    assert cj.split_half(c) == 옛


def test_jobs_이름은_services_함수_그_자체() -> None:
    assert cj.split_half is me.split_half


@pytest.mark.parametrize("n", [3, 4, 9, 10])
def test_split_half_가운데_날은_두_반에_모두(n: int) -> None:
    c = _곡선(n, 7)
    반 = me.split_half(c)
    가운데 = c[n // 2][0]
    assert 반["first"]["end"] == 가운데 == 반["second"]["start"]
    assert 반["first"]["days"] + 반["second"]["days"] == n + 1
    assert 반["first"]["start"] == c[0][0] and 반["second"]["end"] == c[-1][0]


# ── (c) 코호트 BH m ──────────────────────────────────────────────────────────


def test_m_은_N_최댓값과_줄_수_가운데_큰_쪽() -> None:
    assert me.cohort_m([19, 18, None, 19]) == 19  # N(19) > 줄 수(4)
    assert me.cohort_m([2, 2, 2, None, None]) == 5  # 줄 수(5) > N(2)
    assert me.cohort_m([12]) == 12


def test_N_이_하나도_없으면_m_없음() -> None:
    assert me.cohort_m([None, None, None]) is None  # 줄 수 3 으로 떨어지지 않는다
    assert me.cohort_m([]) is None
    assert me.cohort_m([None, True, float("nan"), "19", 0, -3, 2.5]) is None  # 나쁜 값은 N 아님


def test_정수_꼴_실수_N_은_받는다() -> None:
    assert me.cohort_m([19.0, None]) == 19
