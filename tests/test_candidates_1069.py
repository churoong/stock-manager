"""15회차 후보 기법 — 2026-10-09 사용자 지시분 (docs/factors.md 12.17, docs/infra.md 25.1069).

사전 등록한 정의가 코드와 같은지(창·방향·단위·시점)를 손으로 셀 수 있는 값으로 묶는다. 특히
- IC 가중은 **끝난 기간의 IC 만** 쓴다(한 달 늦게) — 아니면 미래 수익으로 가중을 고른다
- 목표가 분산은 분할 앞뒤 목표가를 같은 축으로 옮긴다
- 개인 순매수는 백만원, 거래대금은 원 — 단위를 맞춘다
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from batch.jobs import backtest as job
from batch.services import backtest as bt
from batch.services import candidate_factors as cf


def test_장기_반전은_1년을_건너뛴_앞_2년() -> None:
    c = [100.0] * (cf.LT_FAR + 1)
    c[-1 - cf.LT_FAR] = 50.0  # 3년 전 50 → 1년 전 100: +100% 오른 종목 → −1.0
    assert cf.lt_reversal(c, kr=False) == -1.0
    assert cf.lt_reversal(c[1:], kr=False) is None  # 757개 미만
    # 국내는 그 창 안 하루 ±30% 밖 움직임(수정 안 된 분할 흔적)이 있으면 NULL — 최근 1년 안은 보지 않는다
    튐 = [100.0] * (cf.LT_FAR + 1)
    튐[300] = 200.0
    assert cf.lt_reversal(튐, kr=True) is None and cf.lt_reversal(튐, kr=False) == 0.0
    최근튐 = [100.0] * (cf.LT_FAR + 1)
    최근튐[-10] = 200.0
    assert cf.lt_reversal(최근튐, kr=True) == 0.0


def test_신용잔고율은_2거래일_앞까지_묵으면_버린다() -> None:
    날 = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"]
    rows = [("2026-03-02", 5.0), ("2026-03-03", 6.0), ("2026-03-05", 9.0)]
    assert cf.credit_balance(rows, 날) == -6.0  # 기준일(03-05) 2거래일 전 = 03-03 까지
    assert cf.credit_balance([("2026-02-01", 5.0)], 날) is None  # 14일보다 묵음
    assert cf.credit_balance(rows, 날[:2]) is None


def test_개인_순매수는_백만원을_원으로() -> None:
    날 = [f"2026-01-{i:02d}" for i in range(1, 21)]
    순 = {d: 10.0 for d in 날}  # 하루 10백만원 = 1천만원
    v = [1e9] * 20  # 하루 거래대금 10억
    assert cf.retail_flow(순, 날, v) == pytest.approx(-0.01)
    assert cf.retail_flow({**순, 날[5]: None}, 날, v) is None
    assert cf.retail_flow(순, 날[:19], v[:19]) is None


def test_목표가_분산은_분할_앞뒤를_같은_축으로() -> None:
    # 01-10 에 1:10 분할 — 그 전 목표가는 원값 10배. 수정 계수(수정 ÷ 원)는 분할 전 0.1, 뒤 1.0
    계수 = {"2026-01-05": 0.1, "2026-01-20": 1.0}
    k = lambda d: 0.1 if d < "2026-01-10" else 1.0  # noqa: E731
    의견 = [("2026-01-05", "가", 1000.0), ("2026-01-20", "나", 100.0), ("2026-01-21", "다", 100.0)]
    assert cf.tp_dispersion(의견, "2026-02-01", k) == pytest.approx(0.0)  # 옮기면 모두 100
    assert 계수  # 문서용
    퍼짐 = [("2026-01-20", "가", 80.0), ("2026-01-20", "나", 100.0), ("2026-01-21", "다", 120.0)]
    assert cf.tp_dispersion(퍼짐, "2026-02-01", lambda d: 1.0) == pytest.approx(-math.sqrt(800 / 3) / 100)
    assert cf.tp_dispersion(퍼짐[:2], "2026-02-01", lambda d: 1.0) is None  # 3곳 미만
    assert cf.tp_dispersion(퍼짐, "2026-06-01", lambda d: 1.0) is None  # 90일 창 밖
    assert cf.tp_dispersion(퍼짐, "2026-02-01", lambda d: None) is None  # 계수 모름


def test_IC_가중은_쌓일수록_IC_쪽으로() -> None:
    고정 = {"value": 20.0, "momentum": 20.0, "quality": 20.0, "growth": 20.0, "risk": 20.0}
    assert cf.ic_weights(고정, {}) == 고정
    hist = {f: [0.0] * 36 for f in 고정} | {"momentum": [0.05] * 36}
    w = cf.ic_weights(고정, hist)
    # λ = 36/72 = 0.5 → 모멘텀 0.5·20 + 0.5·100 = 60, 나머지 0.5·20 = 10
    assert w["momentum"] == pytest.approx(60.0) and w["value"] == pytest.approx(10.0)
    assert sum(w.values()) == pytest.approx(100.0)
    # 모두 0 이하면 현행
    assert cf.ic_weights(고정, {f: [-0.1] * 10 for f in 고정}) == 고정
    assert cf.shrink_missing(90.0, 1, 4) == pytest.approx(60.0) and cf.shrink_missing(None, 1, 4) is None


def _영업일(시작: date, 끝: date) -> list[str]:
    out, d = [], 시작
    while d <= 끝:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_IC_가중은_끝난_기간의_IC_만_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """리밸런스 k 번째에는 끝난 기간이 k−1 개다(첫 기간은 둘째 리밸런스 날 끝나는데 그날은 아직 판단 기준일 뒤)."""
    ids = list(range(1, 41))
    dates = _영업일(date(2026, 1, 1), date(2026, 5, 29))
    prices = {sid: {d: 100.0 * (1 + 0.001 * sid) ** i for i, d in enumerate(dates)} for sid in ids}
    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        SimpleNamespace(stock_id=r["stock_id"], market="KOSPI", sector=None, metrics={"mdd_abs": 0.1}) for r in rows])
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=i.stock_id, factor=f, score=float(i.stock_id), missing_fields=[])
        for i in inputs for f in ("value", "momentum")])  # fmt: skip
    본 = []
    진짜 = cf.ic_weights
    monkeypatch.setattr(job.cf, "ic_weights", lambda fixed, hist: (본.append(len(hist["value"])), 진짜(fixed, hist))[1])
    decide = job.make_strategy("ic_weighted", [{"stock_id": s} for s in ids], {}, 5,
                               {"value": 50.0, "momentum": 50.0}, [])  # fmt: skip
    for t in bt.month_starts(dates):
        cutoff = bt.decision_cutoff(dates, prices, t)
        if cutoff:
            decide(t, bt.PriceView(prices, cutoff))
    # 1월 첫날은 판단할 앞날이 없어 건너뛴다 → 2·3·4·5월 네 번. 2→3월 기간은 4월 판단에서야 끝난 기간이다
    assert 본 == [0, 0, 1, 2]


def test_결측_수축은_재료가_적은_고득점을_끌어내린다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.services import scoring as sc

    등록 = [m.name for m in sc.FACTOR_METRICS["value"]]
    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        SimpleNamespace(stock_id=r["stock_id"], market="KOSPI", sector=None, metrics={"mdd_abs": 0.1}) for r in rows])
    # 1번: 90점이지만 지표 하나로만 낸 점수, 2번: 80점·지표 다 있음
    나머지 = ("quality", "growth", "momentum", "risk")
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=1, factor="value", score=90.0, missing_fields=등록[1:]),
        SimpleNamespace(stock_id=2, factor="value", score=80.0, missing_fields=[]),
        *[SimpleNamespace(stock_id=sid, factor=f, score=50.0, missing_fields=[]) for sid in (1, 2) for f in 나머지]])  # fmt: skip
    prices = {1: {"2026-01-02": 100.0}, 2: {"2026-01-02": 100.0}}
    view = bt.PriceView(prices, "2026-01-02")
    고름 = {s: job.make_strategy(s, [{"stock_id": 1}, {"stock_id": 2}], {}, 1, {f: 20.0 for f in ("value", *나머지)}, [])("2026-01-05", view)
          for s in ("composite", "shrink_missing")}  # fmt: skip
    assert list(고름["composite"]) == [1] and list(고름["shrink_missing"]) == [2]


def test_국내_재료로_세_IC_를_잰다(monkeypatch: pytest.MonkeyPatch) -> None:
    """신용잔고·개인 순매수·목표가 분산이 클수록 다음 달 수익이 낮게 만든 세계 — 세 IC 모두 +1 이어야 한다(방향 −)."""
    ids = list(range(1, 41))
    dates = _영업일(date(2026, 1, 1), date(2026, 3, 2))
    prices = {sid: {d: 100.0 for d in dates} for sid in ids}
    for sid in ids:
        prices[sid]["2026-03-02"] = 100.0 - sid  # 큰 번호일수록 많이 빠진다
    turnover = {sid: {d: 1e9 for d in dates} for sid in ids}
    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        SimpleNamespace(stock_id=r["stock_id"], market="KOSPI", sector=None, metrics={}) for r in rows])
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=i.stock_id, factor="value", score=50.0) for i in inputs])
    ke = job.KrExtra(
        credit={sid: [(d, float(sid)) for d in dates] for sid in ids},
        retail={sid: {d: float(sid) for d in dates} for sid in ids},
        opinions={sid: [("2026-01-20", "가", 100.0), ("2026-01-20", "나", 100.0 + sid), ("2026-01-21", "다", 100.0 - sid)]
                  for sid in ids},
        factors={sid: [(dates[0], 1.0)] for sid in ids},
    )  # fmt: skip
    got = job.factor_ics([{"stock_id": s} for s in ids], {}, prices, dates, ["2026-02-02", "2026-03-02"], {},
                         turnover=turnover, kr_extra=ke)  # fmt: skip
    for name in ("credit_balance", "retail_flow", "tp_dispersion"):
        assert got[name].mean == pytest.approx(1.0), name
    assert set(job.USER_IC_NAMES) <= set(got) and len(job.LOOP_IC_NAMES) == 11
    # 기록에는 사용자 지시분 묶음이 따로
    로그 = job.ic_log(got)
    assert 로그["credit_balance"]["fdr"]["group"] == "user_2026_10_09" and "group" not in 로그["sue"].get("fdr", {})


def test_업종_대형주는_시총_상위_30퍼센트() -> None:
    from batch.services import history as hs

    def m(r: float) -> dict:
        return {"w": {"20": {"since": "s", "stock": r, "market": 0.0}}, "last": 1.0}

    peers = {k: m(0.1 * k) for k in range(2, 11)}  # 2~10번
    caps = {k: float(k) for k in range(1, 11)}  # 번호가 클수록 크다
    ld = hs.leaders(1, m(-0.05), peers, caps)
    # 10종목의 30% = 3종목(8·9·10). 나(1번)는 가장 작아 대형주가 아니다
    assert ld["n"] == 3 and ld["of"] == 10 and not ld["self_leader"]
    assert hs.leaders(10, m(0.5), {k: m(0.1 * k) for k in range(1, 10)}, caps)["self_leader"]
    # 창 시작일이 다른 대형주는 평균에 넣지 않는다
    다른 = {**peers, 10: {"w": {"20": {"since": "x", "stock": 9.0, "market": 0.0}}, "last": 1.0}}
    assert hs.leaders(1, m(-0.05), 다른, caps)["w"]["20"]["leaders"] == pytest.approx(0.85)
    assert ld["w"]["20"]["leaders"] == pytest.approx(0.9) and ld["w"]["20"]["stock"] == -0.05
    assert hs.leaders(1, m(0.0), {2: m(0.1)}, caps) is None  # 업종이 작다
    줄 = hs.move_lines({"w": {}, "leaders": ld})
    assert 줄 == ["업종 대형주(시총 상위 3/10종목, 지난 20거래일): 평균 +90.0% · 이 종목 -5.0%"]


def test_커버리지_개시는_이력이_1년을_덮을_때만() -> None:
    from batch.services import insights as ins

    오늘 = date(2026, 10, 9)
    rows = [{"broker": "가", "first_date": "2025-10-20"}, {"broker": "나", "first_date": "2026-09-01"}]
    assert ins.initiations(rows, 오늘) == [{"broker": "나", "date": "2026-09-01"}]
    # 가장 이른 의견이 창 시작(2025-10-09)에서 30일 넘게 늦으면(100행 상한에 잘린 종목) 모른다
    assert ins.initiations([{"broker": "가", "first_date": "2026-03-01"}, rows[1]], 오늘) == []
    assert ins.initiations([], 오늘) == []
