"""38회차 P1′ `buffer_2n` — 순위 버퍼 고르기와 composite 대비 판정 (docs/factors.md 12.41, docs/infra.md 25.2179).

사전 등록(2026-10-23): 든 종목은 종합 순위 2N 안이면 남기고 빈자리를 순위대로 채워 N 종목 동일가중. 판정은 같은 실행의
composite 와 — CAGR·샤프 높음, MDD 악화 ≤ 2%p, 차이 월 초과 앞·뒤 반 양, 리밸런스 ≥ 36, 회전율이 낮지 않으면 판정 불가.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from batch.jobs import backtest as job
from batch.services import backtest as bt
from batch.services import buffer2n as b2n


def test_든_종목은_2N_안이면_남기고_빈자리는_순위대로() -> None:
    점수 = {i: 100.0 - i for i in range(1, 51)}  # 순위 = 번호
    # 든 종목 3 (순위 3)·35 (순위 35 ≤ 2N=40)·45 (순위 45 > 40) — n = 20
    고름 = b2n.pick(점수, {3, 35, 45}, 20)
    assert 35 in 고름 and 45 not in 고름 and 3 in 고름
    # 남긴 둘 + 순위 1~19 가운데 3 을 뺀 18 = 20 종목, 동일가중
    assert sorted(고름) == sorted({3, 35} | {i for i in range(1, 20) if i != 3})
    assert len(고름) == 20 and all(w == pytest.approx(1 / 20) for w in 고름.values())


def test_처음엔_composite_상위_N_과_같다() -> None:
    점수 = {i: float(i % 7) + i / 1000 for i in range(1, 60)}
    assert b2n.pick(점수, set(), 10) == bt.top_n_equal_weight(점수, 10)


def test_경계_2N_은_남기고_2N_더하기_1_은_판다() -> None:
    점수 = {i: 100.0 - i for i in range(1, 51)}
    assert 40 in b2n.pick(점수, {40}, 20) and 41 not in b2n.pick(점수, {41}, 20)


def test_점수가_없어진_든_종목은_팔고_NaN_은_순위에_없다() -> None:
    점수: dict[int, float | None] = {1: 9.0, 2: None, 3: float("nan"), 4: 7.0, 5: 6.0}
    assert b2n.ranked(점수) == [1, 4, 5]
    assert set(b2n.pick(점수, {2, 3}, 2)) == {1, 4}


def test_같은_점수는_종목_번호_순() -> None:
    assert b2n.ranked({5: 1.0, 2: 1.0, 9: 2.0}) == [9, 2, 5]


def test_든_것이_n_을_넘으면_순위대로_n_개() -> None:
    점수 = {i: 100.0 - i for i in range(1, 30)}
    assert set(b2n.pick(점수, {1, 2, 3, 4, 5}, 3)) == {1, 2, 3}


def test_든_것이_n_을_넘으면_번호가_아니라_순위로_자른다() -> None:
    """엔진 보유(정지로 못 판 종목 포함, 25.2183)는 n 을 넘을 수 있다 — 남김 정렬 키가 순위인지 묶는다(25.2184,
    교차검증 [참고]: 순위 = 번호인 자료로는 정렬 키를 번호로 바꿔도 통과했다)"""
    점수 = {5: 9.0, 1: 8.0, 4: 7.0, 2: 6.0, 3: 5.0}
    assert set(b2n.pick(점수, {1, 2, 3, 4, 5}, 3)) == {5, 1, 4}


def test_n_이_0_이하면_거절() -> None:
    with pytest.raises(ValueError):
        b2n.pick({1: 1.0}, set(), 0)


def test_빈_점수면_빈_비중() -> None:
    assert b2n.pick({}, {1}, 5) == {}


def test_사전_등록_값() -> None:
    """factors.md 12.41 의 값 — 2N·리밸런스 36·MDD 2%p·비교 대상 composite(자유 숫자 0)"""
    assert (b2n.BUFFER_MULT, b2n.JUDGE_MIN_REBALANCES, b2n.JUDGE_MDD_SLACK, b2n.BASE) == (2, 36, 0.02, "composite")


def test_시도_수는_18_이고_후보_전략이다() -> None:
    assert "buffer_2n" in job.STRATEGIES and "buffer_2n" in job.CANDIDATE_STRATEGIES
    assert job.REGISTERED_TRIALS == 18
    assert not b2n.NAME.startswith("composite")  # 웹 strategyKind 가 composite 로 시작하는 이름을 대표 전략으로 묶는다


# ── 판정 ─────────────────────────────────────────────────────────────────────


def _결과(월수익: list[float], 회전: float, 리밸: int = 40) -> bt.BacktestResult:
    """달마다 그 달 마지막 날 한 점 — 월수익이 그대로 `luck.monthly_returns` 에 잡힌다"""
    곡선, eq = [], 1.0
    for i in range(len(월수익) + 1):
        y, m = 2022 + i // 12, i % 12 + 1
        곡선.append((f"{y}-{m:02d}-28", eq))
        if i < len(월수익):
            eq *= 1 + 월수익[i]
    리 = [bt.Rebalance(date=f"{2022 + i // 12}-{i % 12 + 1:02d}-01", weights={}, turnover=회전, cost=0.0)
         for i in range(리밸)]  # fmt: skip
    return bt.BacktestResult(curve=곡선, rebalances=리, costs=bt.Costs())


def _요약(값: dict[int, tuple[float | None, float | None, float | None]]):  # noqa: ANN202
    """결과 객체 id → (cagr, sharpe, mdd) 를 돌려주는 가짜 `summarize`"""
    def summarize(r: bt.BacktestResult, _bench, risk_free_annual=None):  # noqa: ANN001, ANN202
        c, s, d = 값[id(r)]
        return SimpleNamespace(metrics=SimpleNamespace(cagr=c, sharpe=s, mdd=d))
    return summarize


def _판정(버: tuple, 기: tuple, 버월: list[float] | None = None, 버회전: float = 0.2, 리밸: int = 40,
         무위험: float | None = 0.03) -> dict:  # fmt: skip
    b = _결과(버월 if 버월 is not None else [0.012] * 40, 버회전, 리밸)
    c = _결과([0.010] * 40, 0.5, 리밸)
    return b2n.judge({"buffer_2n": b, "composite": c}, 무위험, summarize=_요약({id(b): 버, id(c): 기}))


def test_판정_통과() -> None:
    out = _판정((0.12, 0.8, -0.30), (0.10, 0.7, -0.30))
    assert out["verdict"] == "통과" and out["rebalances"] == 40
    assert out["metrics"]["buffer_2n"]["turnover"] == pytest.approx(0.2)
    assert all(h > 0 for h in out["halves"])


@pytest.mark.parametrize(("버", "까닭"), [
    ((0.10, 0.8, -0.30), "CAGR ≤ composite"),
    ((0.12, 0.7, -0.30), "샤프 ≤ composite"),
    ((0.12, 0.8, -0.3201), "MDD 가 composite 보다 2%p 넘게 나쁨"),
])  # fmt: skip
def test_판정_탈락_칸마다(버: tuple, 까닭: str) -> None:
    out = _판정(버, (0.10, 0.7, -0.30))
    assert out["verdict"] == "탈락" and 까닭 in out["why"]


def test_MDD_2퍼센트포인트_까지는_괜찮다() -> None:
    assert _판정((0.12, 0.8, -0.3199), (0.10, 0.7, -0.30))["verdict"] == "통과"


def test_뒤_반_월_초과가_음이면_탈락() -> None:
    월 = [0.015] * 20 + [0.008] * 20  # 앞 반은 composite(1.0%)보다 높고 뒤 반은 낮다
    out = _판정((0.12, 0.8, -0.30), (0.10, 0.7, -0.30), 버월=월)
    assert out["verdict"] == "탈락" and "앞·뒤 반 월 초과가 모두 양이 아님" in out["why"]
    assert out["halves"][0] > 0 > out["halves"][1]


def test_앞_반_월_초과가_음이면_탈락() -> None:
    """앞·뒤 반 **모두** 양이어야 한다 — 뒤 반만 보는 돌연변이를 막는다(25.2184, 교차검증 [권고])"""
    월 = [0.008] * 20 + [0.015] * 20  # 앞 반은 composite(1.0%)보다 낮고 뒤 반은 높다
    out = _판정((0.12, 0.8, -0.30), (0.10, 0.7, -0.30), 버월=월)
    assert out["verdict"] == "탈락" and "앞·뒤 반 월 초과가 모두 양이 아님" in out["why"]
    assert out["halves"][0] < 0 < out["halves"][1]


def test_회전율이_낮지_않으면_구현_의심으로_판정_불가() -> None:
    out = _판정((0.12, 0.8, -0.30), (0.10, 0.7, -0.30), 버회전=0.5)
    assert out["verdict"] == "판정 불가" and "구현 의심" in out["why"]


def test_리밸런스_36_미만이면_판정_불가() -> None:
    out = _판정((0.12, 0.8, -0.30), (0.10, 0.7, -0.30), 리밸=35)
    assert out["verdict"] == "판정 불가" and out["why"] == "리밸런스 35 < 36"
    assert _판정((0.12, 0.8, -0.30), (0.10, 0.7, -0.30), 리밸=36)["verdict"] == "통과"


def test_샤프가_비면_무위험수익률_까닭() -> None:
    out = _판정((0.12, None, -0.30), (0.10, 0.7, -0.30), 무위험=None)
    assert out["verdict"] == "판정 불가" and "buffer_2n.sharpe" in out["why"] and "무위험수익률 설정 없음" in out["why"]


def test_견줄_전략이_없으면_판정_불가() -> None:
    assert b2n.judge({"composite": _결과([0.01] * 40, 0.5)}, 0.03)["why"] == "견줄 전략 없음"


def test_실행_감싸개는_계산_실패를_판정_불가로() -> None:
    out = job._buffer_judge({"buffer_2n": object(), "composite": object()}, 0.03)  # type: ignore[dict-item]
    assert out["verdict"] == "판정 불가" and out["why"].startswith("계산 실패: ")


# ── 전략 함수가 리밸런스 사이에 든 종목을 기억한다 ───────────────────────────────


def test_전략_함수는_지난_리밸런스에_고른_종목을_버퍼로_남긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """두 번째 리밸런스에서 순위가 N 밖·2N 안으로 내려간 든 종목을 남긴다 — composite 는 같은 날 판다.
    기억(`버퍼_든`)을 지우면 composite 와 같아져 이 시험이 깨진다"""
    from batch.services import scoring as sc

    차례 = {"n": 0}
    # N = 2, 2N = 4. 첫 날 상위 둘은 1·2, 둘째 날 순위는 4·5·2·1·6·3
    날점수 = [{1: 90.0, 2: 80.0, 3: 70.0, 4: 60.0, 5: 50.0, 6: 40.0},
             {4: 95.0, 5: 85.0, 2: 75.0, 1: 65.0, 6: 55.0, 3: 45.0}]  # fmt: skip

    def 입력(universe, *_a, **_k):  # noqa: ANN001, ANN202
        return [sc.StockInput(stock_id=r["stock_id"], market="KOSPI") for r in universe]

    def 점수(inputs, *_a, **_k):  # noqa: ANN001, ANN202
        표 = 날점수[차례["n"]]
        차례["n"] += 1
        return [sc.FactorResult(stock_id=i.stock_id, factor=f, zscore=0.0, score=표[i.stock_id], peer_group="g",
                                peer_size=6, missing_fields=[], raw={})
                for i in inputs for f in ("value", "quality", "growth", "momentum", "risk")]  # fmt: skip

    monkeypatch.setattr(job, "build_pit_inputs", 입력)
    monkeypatch.setattr(job.sc, "score_factors", 점수)

    class 창:
        def __init__(self, cutoff: str) -> None:
            self.cutoff = cutoff

        def stocks(self) -> list[int]:
            return [1, 2, 3, 4, 5, 6]

        def last_close(self, _sid: int) -> float:
            return 1.0

    유니버스 = [{"stock_id": i} for i in range(1, 7)]
    가중 = {f: 20.0 for f in ("value", "quality", "growth", "momentum", "risk")}

    def 돌림(전략: str) -> list[dict[int, float]]:
        차례["n"] = 0
        decide = job.make_strategy(전략, 유니버스, {}, 2, 가중, [])
        return [decide("2024-01-31", 창("2024-01-30")), decide("2024-02-29", 창("2024-02-28"))]  # type: ignore[arg-type]

    첫, 둘 = 돌림("buffer_2n")
    assert set(첫) == {1, 2}
    # 둘째 날 순위: 4·5·2·1·6·3 — 1 은 4위(≤ 2N = 4)라 남고, 2 는 3위라 남는다 → 4·5 를 사지 않는다
    assert set(둘) == {1, 2}
    c첫, c둘 = 돌림("composite")
    assert set(c첫) == {1, 2} and set(c둘) == {4, 5}  # composite 는 갈아탄다


def test_전략_함수는_composite_와_같은_종합_순위로_고른다(monkeypatch: pytest.MonkeyPatch) -> None:
    """12.41 "composite 와 같은 순위" — 팩터마다 점수가 달라 어느 팩터 하나로 줄 세워도 종합과 다른 자료로 묶는다
    (25.2184, 교차검증 [권고]: 다섯 팩터가 같은 값이면 팩터 하나로 세우는 돌연변이가 통과했다).
    종합 상위 둘 = {1, 2}, 팩터마다 상위 둘은 {3,1}·{4,1}·{3,2}·{4,2}·{3,1}"""
    from batch.services import scoring as sc

    팩터점수 = {
        "value": {1: 70.0, 2: 69.0, 3: 100.0, 4: 0.0},
        "quality": {1: 70.0, 2: 69.0, 3: 0.0, 4: 100.0},
        "growth": {1: 69.0, 2: 70.0, 3: 100.0, 4: 0.0},
        "momentum": {1: 69.0, 2: 70.0, 3: 0.0, 4: 100.0},
        "risk": {1: 70.0, 2: 69.0, 3: 100.0, 4: 0.0},
    }

    def 입력(universe, *_a, **_k):  # noqa: ANN001, ANN202
        return [sc.StockInput(stock_id=r["stock_id"], market="KOSPI") for r in universe]

    def 점수(inputs, *_a, **_k):  # noqa: ANN001, ANN202
        return [sc.FactorResult(stock_id=i.stock_id, factor=f, zscore=0.0, score=팩터점수[f][i.stock_id],
                                peer_group="g", peer_size=4, missing_fields=[], raw={})
                for i in inputs for f in 팩터점수]  # fmt: skip

    monkeypatch.setattr(job, "build_pit_inputs", 입력)
    monkeypatch.setattr(job.sc, "score_factors", 점수)

    class 창:
        cutoff = "2024-01-30"
        holding = frozenset()

        def stocks(self) -> list[int]:
            return [1, 2, 3, 4]

    가중 = {f: 20.0 for f in 팩터점수}
    고름 = {전략: set(job.make_strategy(전략, [{"stock_id": i} for i in range(1, 5)], {}, 2, 가중, [])(
        "2024-01-31", 창()))  # type: ignore[arg-type]
        for 전략 in ("buffer_2n", "composite")}  # fmt: skip
    assert 고름["buffer_2n"] == 고름["composite"] == {1, 2}
