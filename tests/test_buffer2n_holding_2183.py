"""buffer_2n 의 "든 종목" 은 엔진이 실제로 든 것(`view.holding`) — docs/infra.md 25.2183 (25.2179 교차검증 [권고]).

사전 등록 정의(factors.md 12.41 "이미 든 종목은 순위 2N 안이면 남기고")와 달리 지난번 **고른** 목록을 기억했다.
그래서 그날 체결가가 없어 엔진이 못 산 종목에도 버퍼를 줬고, 정지로 못 판 종목은 든 것으로 보지 않았다(25.1231 과 같은 꼴).
실제 엔진(`bt.simulate`)으로 묶는다.
"""

from __future__ import annotations

import pytest

from batch.jobs import backtest as job
from batch.services import backtest as bt
from batch.services import scoring as sc

FACTORS = ("value", "quality", "growth", "momentum", "risk")


def _돌림(monkeypatch: pytest.MonkeyPatch, 날점수: list[dict[int, float]], prices: dict[int, dict[str, float]],
         dates: list[str], 리밸: list[str]) -> list[set[int]]:  # fmt: skip
    차례 = {"n": 0}

    def 입력(universe, *_a, **_k):  # noqa: ANN001, ANN202
        return [sc.StockInput(stock_id=r["stock_id"], market="KOSPI") for r in universe]

    def 점수(inputs, *_a, **_k):  # noqa: ANN001, ANN202
        표 = 날점수[차례["n"]]
        차례["n"] += 1
        return [sc.FactorResult(stock_id=i.stock_id, factor=f, zscore=0.0, score=표[i.stock_id], peer_group="g",
                                peer_size=len(표), missing_fields=[], raw={})
                for i in inputs for f in FACTORS]  # fmt: skip

    monkeypatch.setattr(job, "build_pit_inputs", 입력)
    monkeypatch.setattr(job.sc, "score_factors", 점수)
    decide = job.make_strategy("buffer_2n", [{"stock_id": s} for s in prices], {}, 2, {f: 20.0 for f in FACTORS}, [])
    고른: list[set[int]] = []

    def 기록(t: str, view: bt.PriceView) -> dict[int, float]:
        w = decide(t, view)
        고른.append(set(w))
        return w

    bt.simulate(prices, dates, 리밸, 기록, bt.Costs.zero())
    return 고른


def test_그날_체결가가_없어_못_산_종목에는_버퍼를_주지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02", "2024-02-29", "2024-03-01", "2024-03-04"]
    prices = {s: {d: 10.0 for d in dates} for s in range(1, 6)}
    del prices[2]["2024-02-01"]  # 2월 체결일에 2 는 시세가 없다 — 엔진이 못 산다
    # N = 2, 2N = 4. 2월 상위 둘 1·2, 3월 순위 4·5·1·2·3
    날점수 = [{1: 90.0, 2: 80.0, 3: 70.0, 4: 60.0, 5: 50.0}, {4: 95.0, 5: 85.0, 1: 75.0, 2: 65.0, 3: 55.0}]
    첫, 둘 = _돌림(monkeypatch, 날점수, prices, dates, ["2024-02-01", "2024-03-01"])
    assert 첫 == {1, 2}
    # 엔진이 든 것은 1 뿐 — 1(3위 ≤ 4)은 남기고 빈자리는 순위대로 4. 고른 목록으로 세면 2(4위)도 남아 {1, 2} 였다
    assert 둘 == {1, 4}


def test_정지로_못_판_종목은_든_것이라_2N_안이면_남긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02", "2024-02-29", "2024-03-01", "2024-03-04",
             "2024-03-29", "2024-04-01", "2024-04-02"]  # fmt: skip
    prices = {s: {d: 10.0 for d in dates} for s in range(1, 8)}
    del prices[2]["2024-03-01"]  # 3월 체결일에 2 는 거래정지 — 엔진이 못 판다
    날점수 = [
        {1: 90.0, 2: 80.0, 3: 70.0, 4: 60.0, 5: 50.0, 6: 40.0, 7: 30.0},  # 2월: 1·2
        {3: 95.0, 4: 90.0, 5: 85.0, 6: 80.0, 7: 75.0, 1: 70.0, 2: 65.0},  # 3월: 1·2 가 2N 밖 → 3·4 로
        {5: 95.0, 6: 90.0, 2: 85.0, 3: 80.0, 4: 75.0, 1: 70.0, 7: 65.0},  # 4월: 2 는 3위, 3 은 4위, 4 는 5위
    ]
    첫, 둘, 셋 = _돌림(monkeypatch, 날점수, prices, dates, ["2024-02-01", "2024-03-01", "2024-04-01"])
    assert 첫 == {1, 2} and 둘 == {3, 4}
    # 4월 엔진 보유는 {2, 3, 4}(2 는 3월에 못 팔았다). 2N 안인 2·3 을 남긴다. 고른 목록({3, 4})으로 세면 {3, 5} 였다
    assert 셋 == {2, 3}


def test_문서의_예약_기본_실행_시도_수가_전략_수와_같다() -> None:
    """`STRATEGIES` 가 늘면 backtest.md 9장·luck.judge 설명의 "이 실행 n 개" 도 함께 고친다(25.2179 교차검증 [권고])"""
    from pathlib import Path

    from batch.services import luck

    n = len(job.STRATEGIES) - 1
    글 = (Path(__file__).resolve().parent.parent / "docs" / "backtest.md").read_text(encoding="utf-8")
    assert f"예약 기본 실행에서 {n}(벤치마크를 뺀 전략 {n} " in 글
    assert f"장기 문턱까지 돌리면 {n + len(job.DEFAULT_LONG_THRESHOLDS)}." in 글
    설명 = luck.judge.__doc__ or ""
    assert f"이 실행 {n}개" in 설명 and f"하한이 {job.REGISTERED_TRIALS} " in 설명
