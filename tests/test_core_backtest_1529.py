"""핵심 H 백테스트 조각 1b — `services/core_backtest` (docs/backtest.md 10장, docs/infra.md 25.1529).

지키는 것
- **미끼**: cutoff 뒤 가격·재무(새 해·정정)·배당·지수를 붙여도 하루 판단(`DayView`)이 같다. 미끼마다 **양성 대조** —
  같은 미끼를 cutoff 이하로 옮기면 결과가 바뀐다(미끼가 물릴 자리를 실제로 지나간다는 증거)
- 원장 경계: 20번째 뒤 종가가 cutoff 이하 **그리고** 결정일부터 28일 — 둘 다 있어야 성과가 보인다
- 장기 PBR 하위 20% 뒤집기(`buy_zone`) · 자료 없는 부서(정족수·공시 "자료 없음") · 유니버스 순서와 무관 · 주 1회 캐시 ·
  cutoff 순서 오류
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta
from functools import cache

import pytest

from batch.jobs import backtest as jb
from batch.jobs import signals as signals_job
from batch.services import backtest as bt
from batch.services import company as co
from batch.services import core_backtest as cb
from batch.services import holding as hold
from batch.services import paper as pp
from batch.services import scoring as sc
from batch.services import signals as sg


def _영업일(시작: str, n: int) -> list[str]:
    d, out = date.fromisoformat(시작), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


#: 820 거래일(2021-01-04 ~ 2024-02 하순) + 미끼용 뒤 25일. 사업보고서는 FY2017~2022(이듬해 3-15 접수)
전체날 = _영업일("2021-01-04", 845)
날 = 전체날[:820]
마지막 = 날[-1]
뒤날 = 전체날[820:]
N = 40
#: 원장 가중치까지 움직이는 판단 구간 — 마지막 50 거래일
구간 = list(range(770, 820))


@cache
def _기본() -> tuple:
    rng = random.Random(7)
    universe, prices, turn, snaps, divs = [], {}, {}, {}, {}
    for i in range(1, N + 1):
        universe.append({"stock_id": i, "ticker": f"{i:06d}", "name": f"종목{i}",
                         "market": "KOSPI" if i % 3 else "KOSDAQ", "sector": ["반도체", "은행", "화학", "유통"][i % 4],
                         "currency": "KRW", "listed_shares": 10_000_000})  # fmt: skip
        p, drift, vol = 10_000.0, rng.uniform(-0.0005, 0.0015), rng.uniform(0.01, 0.03)
        prices[i], turn[i] = {}, {}
        for k, d in enumerate(날):
            p *= math.exp(drift + vol * rng.gauss(0, 1))
            prices[i][d] = round(p, 2)
            # 거래대금 물결 — 30 거래일마다 1.7배로 올라 종목마다 엇갈려 단기(수급) 조건이 돌아가며 선다
            turn[i][d] = 1e9 * rng.uniform(0.8, 1.2) * (1.7 if (k + 7 * i) // 30 % 2 == 0 else 1.0)
        rev, g, 마진 = rng.uniform(1e11, 1e12), rng.uniform(-0.1, 0.4), rng.uniform(0.05, 0.15)
        snaps[i] = []
        for fy in range(2017, 2023):
            rev *= 1 + g + rng.uniform(-0.05, 0.05)
            oi = rev * 마진 * rng.uniform(0.98, 1.02)
            snaps[i].append({"as_of_date": f"{fy + 1}-03-15", "fiscal_year": fy, "receipt_no": f"{fy}{i:04d}",
                             "consolidated": True, "values": {
                                 "revenue": rev, "operating_income": oi, "net_income": oi * 0.7,
                                 "total_equity": rev * rng.uniform(0.3, 1.5), "total_assets": rev * 2,
                                 "total_liabilities": rev * 0.8, "currency": "KRW"}})  # fmt: skip
        divs[i] = [("2023-03-20", 2022, rev * 0.01)]
    idx: dict[str, dict[str, float]] = {"KOSPI": {}, "KOSDAQ": {}}
    a, b = 2500.0, 800.0
    for d in 날:
        a *= math.exp(0.0003 + 0.01 * rng.gauss(0, 1))
        b *= math.exp(0.0002 + 0.012 * rng.gauss(0, 1))
        idx["KOSPI"][d], idx["KOSDAQ"][d] = a, b
    return universe, prices, turn, snaps, divs, idx


def _복사() -> tuple:
    u, p, t, s, dv, ix = _기본()
    return ([dict(r) for r in u], {k: dict(v) for k, v in p.items()}, {k: dict(v) for k, v in t.items()},
            {k: [dict(r, values=dict(r["values"])) for r in v] for k, v in s.items()},
            {k: list(v) for k, v in dv.items()}, {k: dict(v) for k, v in ix.items()})  # fmt: skip


def _돌림(data: tuple, ks: list[int], **kw) -> list[cb.DayView]:
    u, p, t, s, dv, ix = data
    m = cb.CoreMarket(u, s, ix, dividends_by_stock=dv, **kw)
    pre = (bt.SortedSeries(p), bt.SortedSeries(t), bt.SortedSeries(None))
    return [m.at(bt.PriceView(p, 전체날[k - 1], turnover=t, presorted=pre), 전체날[k]) for k in ks]


@cache
def _기본_구간() -> tuple[cb.DayView, ...]:
    return tuple(_돌림(_복사(), 구간))


def _미끼_전부(data: tuple) -> tuple:
    """마지막 cutoff 뒤에만 있는 미끼 — 가격·거래대금·새 사업연도·정정·배당·지수"""
    u, p, t, s, dv, ix = data
    for i in p:
        for k, d in enumerate(뒤날):
            p[i][d] = 1_000.0 * (1 + k) * (3 if i % 2 else 0.2)
            t[i][d] = 9e12
        s[i].append({"as_of_date": "2024-03-28", "fiscal_year": 2023, "receipt_no": "9" * 8, "consolidated": True,
                     "values": {**s[i][-1]["values"], "revenue": s[i][-1]["values"]["revenue"] * 5,
                                "operating_income": s[i][-1]["values"]["operating_income"] * 9}})  # fmt: skip
        s[i].append({**s[i][-2], "as_of_date": "2024-03-05", "receipt_no": "8" * 8,
                     "values": {**s[i][-2]["values"], "revenue": 1.0, "total_equity": 1.0}})
        dv[i].append(("2024-03-20", 2023, 1e15))
    for code in ix:
        for k, d in enumerate(뒤날):
            ix[code][d] = 1.0 + k
    return data


def test_표본이_실제로_판단을_낸다() -> None:
    """신호·표·H 고름·원장 성과가 비면 아래 미끼 시험이 공짜로 통과한다."""
    dvs = _기본_구간()
    assert sum(len(d.rows) for d in dvs) > 50
    assert any(d.picks for d in dvs) and all(d.population for d in dvs if d.rows)
    assert {h for d in dvs for h in (r.horizon for r in d.rows)} >= {"short", "mid"}
    assert any(c != "B" and v for d in dvs for c, v in d.votes.items())
    # 원장 성과가 차서 회사 가중치가 1 에서 움직였다
    assert any(w != 1.0 for w in dvs[-1].company_weights.values())


def test_미끼_뒤_자료를_붙여도_매일_같다() -> None:
    """**이 파일의 이유.** cutoff 뒤 가격·재무(새 해·정정)·배당·지수를 붙여도 50 거래일 판단이 한 칸도 안 바뀐다."""
    미끼 = _돌림(_미끼_전부(_복사()), 구간)
    for 기본, 다른 in zip(_기본_구간(), 미끼, strict=True):
        assert 기본 == 다른, f"{기본.cutoff}: cutoff 뒤 자료가 판단을 바꿨다 — look-ahead"


# ── 양성 대조: 같은 미끼를 cutoff 이하로 옮기면 바뀐다 ────────────────────────
대조_k = 800
대조_cutoff = 전체날[대조_k - 1]


def _하루(data: tuple) -> cb.DayView:
    return _돌림(data, [대조_k])[0]


@cache
def _대조_기본() -> cb.DayView:
    return _하루(_복사())


def _신호_종목(dv: cb.DayView) -> int:
    assert dv.rows, "대조 날에 신호가 없다 — 표본을 다시 고른다"
    return dv.rows[0].stock_id


def test_대조_가격() -> None:
    data = _복사()
    sid = _신호_종목(_대조_기본())
    data[1][sid][대조_cutoff] *= 1.5
    assert _하루(data) != _대조_기본()


def test_대조_새_사업연도() -> None:
    data = _복사()
    sid = _신호_종목(_대조_기본())
    s = data[3][sid]
    s.append({**s[-1], "as_of_date": 대조_cutoff, "fiscal_year": 2023, "receipt_no": "9" * 8,
              "values": {**s[-1]["values"], "revenue": s[-1]["values"]["revenue"] * 5}})
    다른 = _하루(data)
    assert 다른.inputs[sid].fiscal_year == 2023 != _대조_기본().inputs[sid].fiscal_year


def test_대조_정정() -> None:
    data = _복사()
    sid = _신호_종목(_대조_기본())
    s = data[3][sid]
    s.append({**s[-1], "as_of_date": 대조_cutoff, "receipt_no": "8" * 8,
              "values": {**s[-1]["values"], "revenue": s[-1]["values"]["revenue"] * 3}})
    다른 = _하루(data)
    assert 다른.inputs[sid].revenue_growth != _대조_기본().inputs[sid].revenue_growth


def test_대조_배당() -> None:
    data = _복사()
    for i in data[4]:
        data[4][i].append((대조_cutoff, 2023, 1e13 * (i % 7)))
    assert _하루(data).factor_scores != _대조_기본().factor_scores


def test_대조_지수() -> None:
    data = _복사()
    for code in data[5]:
        for d in 날[대조_k - 30 : 대조_k]:
            data[5][code][d] = 1.0
    다른 = _하루(data)
    assert 다른.regimes["KOSPI"].state == "bear" != _대조_기본().regimes["KOSPI"].state
    assert 다른.regime_factors != _대조_기본().regime_factors


# ── 원장 경계 ────────────────────────────────────────────────────────────────


def _원장_창(cutoff: str, step: int) -> bt.PriceView:
    """2023-01-01 부터 `step` 달력일마다 종가(1번째 100 → n번째 100+n), 지수는 날마다 1000+날수"""
    시작 = date(2023, 1, 1)
    가격 = {1: {(시작 + timedelta(days=k * step)).isoformat(): 100.0 + k for k in range(200)}}
    return bt.PriceView(가격, cutoff)


def _원장_지수(cutoff: str) -> list[tuple[str, float]]:
    시작 = date(2023, 1, 1)
    return [(d, 1000.0 + k) for k in range(400) if (d := (시작 + timedelta(days=k)).isoformat()) <= cutoff]


def _보이나(cutoff: str, step: int) -> list[dict]:
    led = cb.HoldingLedger()
    led.add("2023-01-02", 1, "pick", "지주사 합의", {"A": 1.0, "B": 1.0}, 100.0, "2023-01-01")
    return led.records(_원장_창(cutoff, step), _원장_지수(cutoff), cutoff)


def test_원장_28일_문() -> None:
    """날마다 종가 — 20번째 뒤 종가(01-21)는 일찍 차지만 결정일(01-02)부터 28일(01-30) 전에는 안 보인다."""
    assert pp.ATTRIBUTION_HORIZON == 20 and pp.HORIZON_CALENDAR_DAYS[20] == 28
    assert _보이나("2023-01-29", 1) == []
    [r] = _보이나("2023-01-30", 1)
    assert r["ret_20d"] == pytest.approx(20.0)  # 120 / 100
    assert r["bench_20d"] == pytest.approx((1020 / 1000 - 1) * 100)  # 01-21 지수 ÷ 01-01 지수
    assert json.loads(r["detail_json"])["holding"]["votes"] == {"A": 1.0, "B": 1.0}


def test_원장_20번째_종가_문() -> None:
    """이틀마다 종가 — 28일이 지나도 20번째 뒤 종가(02-10)가 **cutoff 앞**에 없으면 안 보인다.
    cutoff 날 종가 자체로는 아직이다 — 운영은 그 종가를 다음 실행 끝에야 채운다(25.1531, 반박 검증)"""
    assert _보이나("2023-02-09", 2) == []
    assert _보이나("2023-02-10", 2) == []
    [r] = _보이나("2023-02-11", 2)
    assert r["ret_20d"] == pytest.approx(20.0)


def test_원장_휴장이_끼면_하루_늦게() -> None:
    """반박 검증 재현 — 평일만 거래·2024-01-15 휴장. 결정 01-08(기준 01-05): 20번째 종가 02-05 = 결정 뒤 28일째.
    운영 결정일 02-06 의 회의는 02-05 실행 끝에 채운 성과를 읽는데 그 실행의 종가는 02-02 까지 — 아직 안 보인다"""
    날들, d = [], date(2024, 1, 2)
    while d <= date(2024, 2, 20):
        if d.weekday() < 5 and d != date(2024, 1, 15):
            날들.append(d.isoformat())
        d += timedelta(days=1)
    가격 = {1: {x: 100.0 + k for k, x in enumerate(날들)}}
    지수 = [(x, 1000.0) for x in 날들]

    def 본다(cutoff: str) -> list[dict]:
        led = cb.HoldingLedger()
        led.add("2024-01-08", 1, "pick", "x", {"A": 1.0}, 가격[1]["2024-01-05"], "2024-01-05")
        return led.records(bt.PriceView(가격, cutoff), [x for x in 지수 if x[0] <= cutoff], cutoff)

    뒤 = [x for x in 날들 if x > "2024-01-05"]
    assert 뒤[19] == "2024-02-05"
    assert 본다("2024-02-05") == []  # 결정 02-06 — 운영도 아직
    assert len(본다("2024-02-06")) == 1  # 결정 02-07 — 02-06 실행 끝에 채운 것


def test_원장_365일_창과_빼기() -> None:
    led = cb.HoldingLedger()
    led.add("2023-01-02", 1, "pick", "x", {"A": 1.0}, 100.0, "2023-01-01")
    led.add("2023-01-03", 1, "skip", "x", {"B": 1.0}, 100.0, "2023-01-01")
    view, 지수 = _원장_창("2023-06-30", 1), _원장_지수("2023-06-30")
    assert len(led.records(view, 지수, "2023-06-30")) == 2
    assert led.records(view, 지수, "2024-01-03") == [r for r in led.records(view, 지수, "2024-01-03")
                                                       if r["decided_on"] >= "2023-01-03"]  # fmt: skip
    assert len(led.records(view, 지수, "2024-01-03")) == 1
    assert led.discard("2023-01-02", 1) == 1
    assert [r["decided_on"] for r in led.records(view, 지수, "2023-06-30")] == ["2023-01-03"]


def test_원장이_가중치를_움직인다() -> None:
    """찬 행이 회사 성적이 되어 `holding.weights` 로 간다 — A 가 지수보다 20%p 잘했고 B 는 못 했다."""
    led = cb.HoldingLedger()
    led.add("2023-01-02", 1, "pick", "x", {"A": 1.5, "B": 1.0}, 100.0, "2023-01-01")
    led.add("2023-01-02", 2, "skip", "x", {"B": 1.0}, 100.0, "2023-01-01")
    view = bt.PriceView({1: {f"2023-01-{d:02d}": 100.0 + d * 2 for d in range(1, 31)},
                         2: {f"2023-01-{d:02d}": 100.0 - d for d in range(1, 31)}}, "2023-01-30")  # fmt: skip
    w, 성적 = hold.weights(led.records(view, _원장_지수("2023-01-30"), "2023-01-30"), ["A", "B"])
    assert 성적["A"]["n"] == 1 and 성적["B"]["n"] == 2 and w["A"] > 1.0 > w["B"]


# ── 장기 뒤집기 ──────────────────────────────────────────────────────────────


def _장기_입력(pbr: float) -> sg.SignalInput:
    band = sg.Band(p20=1.0, p30=1.2, p50=1.5, p80=2.0, sample=750)
    return sg.SignalInput(stock_id=1, ticker="000001", name="가", market="KOSPI", closes=[10_000.0] * 60,
                          factor_scores={"quality": 80.0, "value": 80.0}, pbr_now=pbr, band=band,
                          band_close=10_000.0)  # fmt: skip


def test_장기_PBR_하위_20퍼센트는_신호가_없다() -> None:
    """`long_term` 규칙은 PBR ≤ p30 이면 통과하지만 `buy_zone` 이 p20 아래를 뒤집어 버린다 — 핵심 H 는 `evaluate` 를
    그대로 부르므로 이 뒤집기가 들어간다. `jobs/backtest.long_entry` 에는 이 뒤집기가 없다(그래서 쓰지 않는다)."""
    낮음, 사이 = _장기_입력(0.9), _장기_입력(1.1)
    assert sg.long_term(낮음)[0] and sg.long_term(사이)[0]
    assert [s.horizon for s in sg.evaluate(사이)] == ["long"]
    assert [s.horizon for s in sg.evaluate(낮음)] == []


def test_핵심_H_는_장기_뒤집기를_따른다() -> None:
    """구간 안 모든 날 — 장기 신호가 난 종목은 PBR 이 밴드 p20 이상이다."""
    for d in _기본_구간():
        for sid, ss in d.signals.items():
            if any(s.horizon == "long" for s in ss):
                inp = d.inputs[sid]
                assert inp.band is not None and inp.pbr_now >= inp.band.p20


# ── 자료 없는 부서 ───────────────────────────────────────────────────────────


def test_재현_못하는_부서는_자료_없음() -> None:
    """공시는 `gov_covered=False` 라 "사건 없음 — 중립" 이 아니라 "자료 없음". 증권사·수급·감성도 자료 없음"""
    dv = next(d for d in _기본_구간() if any(d.approved.values()))
    for d in dv.agenda:
        assert d.gov_covered is False and d.flow_days == 0 and d.broker_upsides == [] and d.sentiment is None
    for c, items in dv.approved.items():
        for i in items:
            assert i.favor >= co.QUORUM
            찬성 = {k for k, o in i.opinions.items() if o.stance > 0}
            assert 찬성 <= {"research", "quant", "risk", "macro"}
            for k in ("broker", "flows", "governance", "sentiment"):
                if k in i.opinions:
                    assert (i.opinions[k].note or "").startswith("자료 없음"), (c, k)


def test_정족수는_리서치_퀀트_리스크_가운데_둘() -> None:
    """나머지 부서가 자료 없음이면 찬성은 셋 중에서만 — 하나만 찬성하면 부결."""
    def 서류(sid: int, v: float, m: float, mdd: float) -> co.Dossier:
        return co.Dossier(stock_id=sid, ticker=f"{sid:06d}", as_of="2024-01-02", domestic=True, gov_covered=False,
                          factors={"value": v, "quality": v, "growth": v, "momentum": m}, mdd=mdd, volatility=-mdd)
    모집단 = [서류(i, float(i), float(i), -0.1 * i) for i in range(1, 11)]
    모집단 += [서류(11, 99.0, 0.0, -0.9)]  # 리서치만 찬성(퀀트·리스크 반대)
    _items, 가결 = co.meet(모집단, co.CHARTERS["A"], 모집단)
    [x] = [i for i in _items if i.stock_id == 11]
    assert not x.passed and x.opinions["governance"].note.startswith("자료 없음")
    assert x.reason.startswith("위원회:")
    assert all(i.favor >= co.QUORUM for i in 가결)


# ── 결정성·캐시·순서 ─────────────────────────────────────────────────────────


def test_유니버스_순서와_무관() -> None:
    u, p, t, s, dv, ix = _복사()
    rng = random.Random(3)
    rng.shuffle(u)
    s = {k: list(reversed(v)) for k, v in sorted(s.items(), key=lambda kv: -kv[0])}
    p = dict(sorted(p.items(), key=lambda kv: -kv[0]))
    기본 = _돌림(_복사(), 구간[-12:])
    섞음 = _돌림((u, p, t, s, dv, ix), 구간[-12:])
    assert any(d.picks for d in 기본) and 섞음 == 기본


def test_팩터_점수는_ISO_주의_첫_cutoff() -> None:
    dvs = _기본_구간()
    for d in dvs:
        assert date.fromisoformat(d.score_cutoff).isocalendar()[:2] == date.fromisoformat(d.cutoff).isocalendar()[:2]
        assert d.score_cutoff <= d.cutoff
    주별 = {}
    for d in dvs:
        주별.setdefault(d.score_cutoff, []).append(d)
    for 첫, ds in 주별.items():
        assert ds[0].cutoff == 첫 and all(x.factor_scores == ds[0].factor_scores for x in ds)
    assert len(주별) >= 9  # 50 거래일 ≈ 10 주


def test_주_중간에_시작하면_그날_낸다() -> None:
    [d] = _돌림(_복사(), [구간[2]])
    assert d.score_cutoff == d.cutoff


def test_cutoff_순서() -> None:
    u, p, t, s, dv, ix = _복사()
    m = cb.CoreMarket(u, s, ix)
    v1, v0 = bt.PriceView(p, 날[800], turnover=t), bt.PriceView(p, 날[799], turnover=t)
    a = m.at(v1, 날[801])
    assert m.at(v1, 날[801]) is a  # 같은 cutoff 는 같은 결과(원장을 두 번 쌓지 않는다)
    with pytest.raises(ValueError, match="거꾸로"):
        m.at(v0, 날[800])
    with pytest.raises(ValueError, match="다른 결정일"):
        m.at(v1, 날[802])
    with pytest.raises(ValueError, match="앞"):
        cb.CoreMarket(u, s, ix).at(v1, 날[799])
    with pytest.raises(ValueError, match="KR·US"):
        cb.CoreMarket(u, s, ix, country="JP")
    # 결정일도 앞으로만 — 같은 결정일에 cutoff 만 바꾸면 그날 원장 줄이 두 번 쌓였다(25.1531, 반박 검증)
    m2 = cb.CoreMarket(u, s, ix)
    m2.at(v0, 날[801])
    with pytest.raises(ValueError, match="앞으로 가지 않는다"):
        m2.at(v1, 날[801])


# ── 반박 검증이 짚은 빈 곳 (25.1531) ─────────────────────────────────────────


def test_기준일_종가가_없는_종목은_신호_입력에서_빠진다() -> None:
    u, p, t, s, dv, ix = _복사()
    k = 구간[0]
    [평소] = _돌림((u, p, t, s, dv, ix), [k])
    assert 1 in 평소.inputs
    del p[1][전체날[k - 1]]  # 1번만 cutoff 날 종가가 없다(정지·늦은 수집)
    [빠짐] = _돌림((u, p, t, s, dv, ix), [k])
    assert 1 not in 빠짐.inputs and set(빠짐.inputs) == set(평소.inputs) - {1}


def test_자본은_종목_통화로_자른다(monkeypatch: pytest.MonkeyPatch) -> None:
    """밴드·지금 PBR 의 자본은 그 종목 통화로만 — `_decide` 가 통화를 넘기는지(도우미만 보던 빈 곳)"""
    u, p, t, s, dv, ix = _복사()
    u[0]["currency"] = "USD"
    본: dict[str, set] = {"pit_equities": set(), "latest": set()}
    원래1, 원래2 = jb.pit_equities, cb.pit_latest_equity

    def 자본들(snaps, cutoff, currency=None):  # noqa: ANN001, ANN202
        본["pit_equities"].add(currency)
        return 원래1(snaps, cutoff, currency=currency)

    def 최신(snaps, cutoff, currency):  # noqa: ANN001, ANN202
        본["latest"].add(currency)
        return 원래2(snaps, cutoff, currency)

    monkeypatch.setattr(jb, "pit_equities", 자본들)
    monkeypatch.setattr(cb, "pit_latest_equity", 최신)
    _돌림((u, p, t, s, dv, ix), 구간[:3])
    assert None not in 본["pit_equities"] and None not in 본["latest"]
    assert 본["pit_equities"] <= {"KRW", "USD"} and "KRW" in 본["pit_equities"]


def test_시점_유니버스가_바뀌면_따른다() -> None:
    u, p, t, s, dv, ix = _복사()
    경계 = 전체날[구간[2] - 1]

    def 편입(cutoff: str) -> set[int]:
        return {r["stock_id"] for r in u} - ({1} if cutoff >= 경계 else set())

    앞, _가운데, 뒤 = _돌림((u, p, t, s, dv, ix), 구간[:3], eligible_at=편입)
    assert 1 in 앞.inputs and 1 not in 뒤.inputs
    assert all(d.stock_id != 1 for d in 뒤.population)


def test_원장에_오늘_줄이_쌓인다() -> None:
    """고른 종목은 pick, 추천받았으나 못 고른 종목은 skip — 둘 다 표를 단다(운영 holding_step)."""
    u, p, t, s, dv, ix = _복사()
    m = cb.CoreMarket(u, s, ix, dividends_by_stock=dv)
    pre = (bt.SortedSeries(p), bt.SortedSeries(t), bt.SortedSeries(None))
    for k in 구간:
        d = m.at(bt.PriceView(p, 전체날[k - 1], turnover=t, presorted=pre), 전체날[k])
        오늘 = [r for r in m.ledger.rows if r.decided_on == d.day]
        assert {r.stock_id for r in 오늘} == {x["stock_id"] for x in d.ranked}
        assert {r.stock_id for r in 오늘 if r.kind == "pick"} == set(d.picks)
        assert all(r.votes and r.ref_date == d.cutoff for r in 오늘)
        for x in d.ranked:
            if x["stock_id"] in d.picks:
                assert x["eligible"]
    assert len(d.picks) <= 5


# ── 운영과 한 정의 ───────────────────────────────────────────────────────────


def test_성장률_정의는_하나() -> None:
    assert signals_job._growth is sc.growth_rate
    지금 = {"revenue": 120.0, "operating_income": 12.0, "currency": "KRW", "report_date": "2024-03-15",
          "consolidated": 1}  # fmt: skip
    전년 = {"revenue": 100.0, "operating_income": 10.0, "currency": "KRW"}
    g = sg.annual_growth(지금, 전년, "2024-04-01", 2023)
    assert g["revenue_growth"] == pytest.approx(0.2) and g["operating_income_growth"] == pytest.approx(0.2)
    assert sg.annual_growth(지금, {**전년, "currency": "USD"}, "2024-04-01", 2023)["revenue_growth"] is None
    assert sg.annual_growth({**지금, "period_days": 200}, 전년, "2024-04-01", 2023)["revenue_growth"] is None
    assert sg.annual_growth({**지금, "revenue": float("nan")}, 전년, "2024-04-01", 2023)["revenue_growth"] is None
    묵음 = sg.annual_growth(지금, 전년, "2025-08-01", 2023)
    assert 묵음["stale_reason"] and 묵음["revenue_growth"] is None and 묵음["stale_report_date"] == "2024-03-15"


def test_시점_성장률은_접수일과_정정을_본다() -> None:
    행 = [{"as_of_date": "2023-03-15", "fiscal_year": 2022, "receipt_no": "1", "consolidated": True,
          "values": {"revenue": 100.0, "operating_income": 10.0}},
         {"as_of_date": "2024-03-15", "fiscal_year": 2023, "receipt_no": "2", "consolidated": True,
          "values": {"revenue": 150.0, "operating_income": 15.0}},
         {"as_of_date": "2024-05-01", "fiscal_year": 2023, "receipt_no": "3", "consolidated": True,
          "values": {"revenue": 130.0, "operating_income": 15.0}}]  # fmt: skip
    assert cb.pit_growth(행, "2024-03-14")["fiscal_year"] == 2022
    assert cb.pit_growth(행, "2024-03-15")["revenue_growth"] == pytest.approx(0.5)
    assert cb.pit_growth(행, "2024-05-01")["revenue_growth"] == pytest.approx(0.3)
    assert cb.pit_growth(행, "2023-03-14") is None


def test_자본_통화() -> None:
    행 = [{"as_of_date": "2023-03-15", "fiscal_year": 2022, "consolidated": True,
          "values": {"total_equity": 100.0, "currency": "KRW"}},
         {"as_of_date": "2024-03-15", "fiscal_year": 2023, "consolidated": True,
          "values": {"total_equity": 7.0, "currency": "USD"}}]  # fmt: skip
    assert jb.pit_equities(행, "2024-12-31") == [("2023-03-15", 100.0, 2022), ("2024-03-15", 7.0, 2023)]
    assert jb.pit_equities(행, "2024-12-31", currency="KRW") == [("2023-03-15", 100.0, 2022)]
    # 현재 PBR 의 자본은 가장 늦은 해가 다른 통화면 모름 — 옛 해로 내려가지 않는다(운영 `_latest_equity`)
    assert cb.pit_latest_equity(행, "2024-12-31", "KRW") is None
    assert cb.pit_latest_equity(행, "2024-03-14", "KRW") == 100.0


def test_미국_시장_판단기는_SP500_과_국외_조사_자료로(monkeypatch: pytest.MonkeyPatch) -> None:
    """깔때기 ⑧-2(25.2056) — 같은 합성 자료를 미국으로 바꿔 달면 원장 성과·사이징 베타·국면이 SP500 을 읽고(KOSPI 는 묻지
    않음), 위원회 조사 자료가 국외(`domestic=False` — 증권사·수급·공시는 "미국 종목 … 모으지 않는다")다. 장부형 판단
    함수(25.2058)와 S·T 리플레이(25.2061)도 미국을 받는다"""
    from batch.services import company as co
    from batch.services import core_strategy as cs

    u, p, t, s, dv, ix = _복사()
    u = [dict(r, market="NASDAQ", currency="USD") for r in u]
    for r in s.values():
        for x in r:
            x["values"]["currency"] = "USD"
    미국지수 = {"SP500": ix["KOSPI"]}
    물은: list[str] = []
    원 = cb.CoreMarket._index_upto

    def 엿봄(self: cb.CoreMarket, code: str, cutoff: str) -> list[tuple[str, float]]:
        물은.append(code)
        return 원(self, code, cutoff)

    monkeypatch.setattr(cb.CoreMarket, "_index_upto", 엿봄)
    국내여부: list[bool] = []
    원_meet = co.meet

    def meet(agenda: list, ch: dict, population: list) -> tuple:
        국내여부.extend(d.domestic for d in [*agenda, *population])
        return 원_meet(agenda, ch, population)

    monkeypatch.setattr(co, "meet", meet)
    m = cb.CoreMarket(u, s, 미국지수, dividends_by_stock=dv, country="US")
    pre = (bt.SortedSeries(p), bt.SortedSeries(t), bt.SortedSeries(None))
    dvs = [m.at(bt.PriceView(p, 전체날[k - 1], turnover=t, presorted=pre), 전체날[k]) for k in 구간[:5]]
    assert "SP500" in 물은 and "KOSPI" not in 물은 and "KOSDAQ" not in 물은, sorted(set(물은))
    assert any(d.rows for d in dvs), "신호가 하나도 없다 — 시험이 미국 길을 지나지 않는다"
    assert 국내여부 and not any(국내여부)
    # 장부형 판단 함수는 25.2058 부터 시장 통화 장부로 미국을 받는다(tests/test_core_us_ledger_2058.py)
    assert cs.CoreStrategy("core_h", m, sessions=날).market.country == "US"
    from batch.services import swing_day_replay as sdr

    # S·T 리플레이도 25.2061 부터 미국을 받는다(tests/test_replay_parity_st_us_2061.py) — KR·US 밖만 막는다
    with pytest.raises(ValueError, match="KR·US"):
        sdr.simulate_account("S", rows_at=lambda _v, _d: [], prices=p, opens=p, dates=날[:3], costs_at=lambda _d: None,
                             stock_cache={}, settings={}, market="JP")  # type: ignore[arg-type]
