"""37회차 A — 52주 고점 근접 전략 `high52` (docs/factors.md 12.40, docs/infra.md 25.1646).

손으로 셀 수 있는 계열로 근접도·국내 빈틈·시장 안 백분위·동점·판정·미래 정보 차단을 확인한다.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from batch.jobs import backtest as job
from batch.services import backtest as bt
from batch.services import high52 as h


def _오름(n: int, 끝: float = 100.0) -> list[float]:
    return [끝 * (0.5 + 0.5 * i / (n - 1)) for i in range(n)]


def test_근접도는_252일_최고_종가_대비_국내는_30퍼센트_밖_날이_있으면_뺀다() -> None:
    계열 = _오름(252)
    assert h.proximity(계열, kr=False) == pytest.approx(1.0)
    assert h.proximity(계열[:-1] + [80.0], kr=False) == pytest.approx(80.0 / 계열[-2])
    assert h.proximity(계열[:251], kr=True) is None  # 252일 미만
    빈틈 = [10.0] * 100 + [50.0] * 152  # 조정 안 된 병합 — 하루 +400%
    assert h.proximity(빈틈, kr=False) == pytest.approx(1.0)
    assert h.proximity(빈틈, kr=True) is None
    # 창 밖(253일 전)의 빈틈은 상관없다 — 창 안 값은 모두 병합 뒤
    assert h.proximity([10.0] + [50.0] * 252, kr=True) == pytest.approx(1.0)
    # 정확히 +30% 는 가격제한 안이다
    assert h.proximity([10.0] * 100 + [13.0] * 152, kr=True) == pytest.approx(1.0)


def test_시장_안_백분위와_작은_시장_모음() -> None:
    큰 = {i: float(i) for i in range(1, 31)}  # KOSPI 30종목
    값 = {**큰, 101: 0.5, 102: 0.9, 103: 0.9}  # 작은 시장 셋
    시장 = {**{i: "KOSPI" for i in 큰}, 101: "BZX", 102: "BZX", 103: "ARCA"}
    p = h.market_percentiles(값, 시장)
    assert p[30] == pytest.approx(1.0) and p[1] == pytest.approx(1 / 30)
    # 30 미만 시장은 한데 — 모아도 셋이라 전 종목 33 안에서 잰다(25.1650): 작은 시장 1등이 1.0 이 되지 않는다
    assert p[101] == pytest.approx(1 / 33) and p[102] == p[103] == pytest.approx(3 / 33)
    # 모은 무리가 30 이상이면 그 무리 안에서
    큰기타 = {**큰, **{200 + i: float(i) for i in range(30)}}
    큰기타시장 = {**{i: "KOSPI" for i in 큰}, **{200 + i: f"작은{i % 3}" for i in range(30)}}
    assert h.market_percentiles(큰기타, 큰기타시장)[229] == pytest.approx(1.0)
    assert 104 not in h.market_percentiles({**값, 104: None}, 시장)


def test_동점은_고정_씨앗_해시로_깨고_갈린_수를_센다() -> None:
    값 = {i: 1.0 for i in range(1, 41)}  # 40종목 모두 신고가
    시장 = {i: "KOSPI" for i in 값}
    고름, 갈린 = h.pick(값, 시장, 10)
    assert len(고름) == 10 and 갈린 == 30 and all(w == pytest.approx(0.1) for w in 고름.values())
    # 넣는 순서가 달라도 같은 종목, 종목 번호 순이 아니다
    역순 = dict(reversed(list(값.items())))
    assert h.pick(역순, 시장, 10)[0].keys() == 고름.keys()
    assert sorted(고름) != list(range(1, 11))
    # 값이 갈리면 동점 없음
    assert h.pick({i: float(i) for i in range(1, 41)}, 시장, 10) == ({i: 0.1 for i in range(31, 41)}, 0)
    with pytest.raises(ValueError):
        h.pick(값, 시장, 0)


def _곡선(월수익: list[float], 시작: date = date(2022, 1, 3)) -> list[tuple[str, float]]:
    """달마다 첫날·말일 두 점 — 월 수익이 그대로 나온다."""
    out, v, d = [], 1.0, 시작
    for r in 월수익:
        out.append((d.isoformat(), v))
        v *= 1 + r
        말 = (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        out.append((말.isoformat(), v))
        d = 말 + timedelta(days=1)
    return out


def _결과(월수익: list[float]) -> bt.BacktestResult:
    곡선 = _곡선(월수익)
    리밸 = [bt.Rebalance(date=d, weights={}, turnover=0.0, cost=0.0) for d, _ in 곡선[0::2]]
    return bt.BacktestResult(curve=곡선, rebalances=리밸, costs=bt.Costs.zero())


def _지표(cagr: float, sharpe: float, mdd: float) -> SimpleNamespace:
    return SimpleNamespace(metrics=SimpleNamespace(cagr=cagr, sharpe=sharpe, mdd=mdd))


def test_판정은_공통_구간에서_둘_다_이겨야_통과() -> None:
    결과 = {"high52": _결과([0.02] * 40), "momentum": _결과([0.01] * 40), "benchmark": _결과([0.005] * 40)}
    지표 = {"high52": (0.2, 1.2, -0.10), "momentum": (0.1, 0.8, -0.09), "benchmark": (0.05, 0.5, -0.12)}

    def 요약(r: bt.BacktestResult, _b: object, risk_free_annual: float | None) -> SimpleNamespace:
        이름 = next(k for k, v in 결과.items() if v.curve[-1] == r.curve[-1])
        return _지표(*지표[이름])

    통과 = h.judge(결과, "2022-01-03", None, 요약)
    assert 통과["verdict"] == "통과" and 통과["rebalances"] == 40
    assert 통과["halves"][0] == pytest.approx(0.01) and 통과["halves"][1] == pytest.approx(0.01)
    # MDD 가 momentum 보다 2%p 넘게 나쁘면 탈락 — 정확히 2%p 는 괜찮다
    지표["high52"] = (0.2, 1.2, -0.115)
    assert h.judge(결과, "2022-01-03", None, 요약)["verdict"] == "탈락"
    지표["high52"] = (0.2, 1.2, -0.11)
    assert h.judge(결과, "2022-01-03", None, 요약)["verdict"] == "통과"
    # 샤프가 momentum 과 같으면 탈락(둘 다 "높아야")
    지표["high52"] = (0.2, 0.8, -0.10)
    판 = h.judge(결과, "2022-01-03", None, 요약)
    assert 판["verdict"] == "탈락" and "샤프 ≤ momentum" in 판["why"]


def test_판정_불가_와_뒤_반이_지면_탈락() -> None:
    짧은 = {"high52": _결과([0.02] * 30), "momentum": _결과([0.01] * 30), "benchmark": _결과([0.0] * 30)}
    판 = h.judge(짧은, "2022-01-03", None)
    assert 판["verdict"] == "판정 불가" and 판["rebalances"] == 30
    assert h.judge(짧은, None, None)["verdict"] == "판정 불가"
    # 공통 구간 시작이 늦으면 그 앞은 세지 않는다
    assert h.judge({**짧은, "high52": _결과([0.02] * 50)}, "2022-01-03", None)["rebalances"] == 50
    assert h.judge({**짧은, "high52": _결과([0.02] * 50)}, "2023-01-01", None)["rebalances"] == 38
    # 앞 반은 이기고 뒤 반은 지는 세계 — 지표가 이겨도 탈락
    결과 = {"high52": _결과([0.05] * 20 + [0.0] * 20), "momentum": _결과([0.01] * 40),
            "benchmark": _결과([0.0] * 40)}  # fmt: skip
    판 = h.judge(결과, "2022-01-03", None, lambda *_a, **_k: _지표(0.1, 1.0, -0.05)
                 if _a[0].curve[-1][1] > 2 else _지표(0.05, 0.5, -0.05))  # fmt: skip
    assert 판["verdict"] == "탈락" and "앞·뒤 반 월 초과가 모두 양이 아님" in 판["why"]


def test_중복_진단_요약() -> None:
    assert h.overlap_summary(0, [0.5, 0.7, None], [0.3, 0.4]) == {
        "vs_momentum_score": 0.6, "vs_momentum_12_1": 0.35, "n": 2, "errors": 0, "open_account": True}
    assert h.overlap_summary(1, [0.5, 0.7], [0.3, 0.4])["open_account"] is None  # 실패가 있으면 정하지 않는다
    assert h.overlap_summary(0, [0.95, 0.9], [0.1, 0.2])["open_account"] is False
    assert h.overlap_summary(0, [], [])["open_account"] is None


def _영업일(n: int) -> list[str]:
    out, d = [], date(2024, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_전략은_기준일까지만_보고_처음_다_채운_날과_진단을_적는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """기준일 뒤 가격을 바꿔도 고른 종목이 같다(미래 정보 없음). 40종목 — 번호가 클수록 마지막에 고점."""
    dates = _영업일(300)
    ids = list(range(1, 41))
    prices = {sid: {d: 100.0 + i * (sid - 20.5) / 50 for i, d in enumerate(dates)} for sid in ids}
    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        SimpleNamespace(stock_id=r["stock_id"], market="KOSPI", sector=None,
                        metrics={"mdd_abs": 0.1, "momentum_12_1": float(r["stock_id"])}) for r in rows])  # fmt: skip
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=float(i.stock_id), missing_fields=[])
        for i in inputs])  # fmt: skip
    자취: dict = {}
    decide = job.make_strategy("high52", [{"stock_id": s} for s in ids], {}, 5, {"momentum": 100.0}, [],
                               trace=자취)  # fmt: skip
    cutoff = dates[280]
    고름 = decide(dates[281], bt.PriceView(prices, cutoff))
    # 오르는 종목(21~40)은 마지막 날이 고점이라 모두 1.0 — 동점을 해시로 깨 다섯
    assert len(고름) == 5 and all(s > 20 for s in 고름)
    assert 자취["full_from"] == dates[281] and 자취["ties_cut"] == 15
    assert 자취["vs_score"][0] is not None and 자취["vs_12_1"][0] is not None
    # 기준일 뒤 가격을 망가뜨려도 같다
    망친 = {sid: {d: (1.0 if d > cutoff else v) for d, v in s.items()} for sid, s in prices.items()}
    다시 = job.make_strategy("high52", [{"stock_id": s} for s in ids], {}, 5, {"momentum": 100.0}, [])
    assert 다시(dates[281], bt.PriceView(망친, cutoff)) == 고름
    # 252일이 안 되면 고를 것이 없고 처음 다 채운 날도 없다
    빈자취: dict = {}
    짧게 = job.make_strategy("high52", [{"stock_id": s} for s in ids], {}, 5, {"momentum": 100.0}, [], trace=빈자취)
    assert 짧게(dates[201], bt.PriceView(prices, dates[200])) == {} and 빈자취.get("full_from") is None


def test_등록된_후보이고_시도_수에_든다() -> None:
    assert "high52" in job.STRATEGIES and "high52" in job.CANDIDATE_STRATEGIES
    assert job.REGISTERED_TRIALS == 17


# --- 25.1650 교차검증 [권고] ---------------------------------------------------------------------------------


def test_중복_문턱은_반올림_전_값으로() -> None:
    assert h.overlap_summary(0, [0.89996] * 3, [0.1] * 3)["open_account"] is True
    assert h.overlap_summary(0, [0.9] * 3, [0.1] * 3)["open_account"] is False


def test_모은_기타도_작으면_전_종목_안에서_잰다() -> None:
    값 = {i: i / 500 for i in range(1, 501)}  # NASDAQ 500 — 0.002 ~ 1.0
    값 |= {601: 0.5, 602: 0.4, 603: 0.3, 604: 0.2}  # 작은 시장 넷 — 모아도 4
    시장 = {**{i: "NASDAQ" for i in range(1, 501)}, 601: "ARCA", 602: "BZX", 603: "IEX", 604: "ARCA"}
    p = h.market_percentiles(값, 시장)
    assert p[500] == pytest.approx(1.0)
    assert p[601] == pytest.approx(254 / 504)  # 0.5 이하: NASDAQ 250 + 작은 넷 — 예전엔 기타 1등이라 1.0
    assert p[604] < p[601] < 1.0


def test_빈칸_까닭과_무위험수익률() -> None:
    결과 = {"high52": _결과([0.02] * 40), "momentum": _결과([0.01] * 40), "benchmark": _결과([0.0] * 40)}
    판 = h.judge(결과, "2022-01-03", None, lambda *_a, **_k: SimpleNamespace(
        metrics=SimpleNamespace(cagr=0.1, sharpe=None, mdd=-0.1)))  # fmt: skip
    assert 판["verdict"] == "판정 불가" and "high52.sharpe" in 판["why"] and "무위험수익률 설정 없음" in 판["why"]


def test_판정_계산이_죽어도_판정_불가로_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    def 죽음(*_a: object, **_k: object) -> dict:
        raise ZeroDivisionError

    monkeypatch.setattr(job.h52, "judge", 죽음)
    판 = job._high52_judge({}, "2022-01-03", None)
    assert 판 == {"start": "2022-01-03", "verdict": "판정 불가", "why": "계산 실패: ZeroDivisionError"}


def test_중복_진단이_죽어도_전략은_돈다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1660 — 판단 함수 안 기록 전용 진단의 예외가 그달 판정 실행 전체를 죽이지 않는다."""
    dates = _영업일(300)
    ids = list(range(1, 41))
    prices = {sid: {d: 100.0 + i * (sid - 20.5) / 50 for i, d in enumerate(dates)} for sid in ids}
    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        SimpleNamespace(stock_id=r["stock_id"], market="KOSPI", sector=None,
                        metrics={"mdd_abs": 0.1, "momentum_12_1": 1.0}) for r in rows])  # fmt: skip
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [])

    def 죽음(*_a: object) -> float:
        raise ZeroDivisionError

    monkeypatch.setattr(job.h52, "overlap", 죽음)
    자취: dict = {}
    decide = job.make_strategy("high52", [{"stock_id": s} for s in ids], {}, 5, {"momentum": 100.0}, [], trace=자취)
    assert len(decide(dates[281], bt.PriceView(prices, dates[280]))) == 5
    assert 자취["vs_score"] == [None] and 자취["vs_12_1"] == [None] and 자취["overlap_errors"] == 2
