"""예상 주가 범위 — 최근 변동성의 기간 구조와 반반 범위 (docs/analysis.md 10.5, docs/infra.md 25.1055).

2026-10-09 사용자: "예상주가가 범위가 너무 넓어. 그정도 예측은 나도 할 수 있겠다". 범위를 **근거 없이** 좁히면
성적표(11장)의 "68% 범위 안 비율" 이 떨어진다. 그래서 좁히는 길은 둘뿐이다 — 지금 흔들림이 작으면 가까운 기간의
범위를 작게(최근 변동성이 장기로 돌아가는 기간 구조), 그리고 반반(50%) 범위를 머리로. 식의 끝점과 단조성을 묶는다.
"""

from __future__ import annotations

import json
import math
from datetime import date

from batch.jobs import metrics as metrics_job
from batch.services import patterns as pt
from batch.services import verdict as vd
from tests.test_metrics_beta import client as client  # noqa: F401 — 픽스처
from tests.test_metrics_beta import 계열, 수익률, 시세넣기
from tests.test_metrics_beta import 기준일 as 지표_기준일

시장 = {"annual": 0.08, "years": 10, "since": "a", "until": "b"}


def test_기간_구조의_끝점과_가운데() -> None:
    L, S = 0.40, 0.20
    assert math.isclose(vd.term_sigma(L, S, 1e-9), S, rel_tol=1e-6)  # 아주 짧은 기간은 지금 변동성
    assert math.isclose(vd.term_sigma(L, S, 1e6), L, rel_tol=1e-3)  # 아주 긴 기간은 장기 변동성
    τ = vd.VOL_HALF_LIFE_MONTHS / 12 / math.log(2)
    assert math.isclose(vd.term_sigma(L, S, τ), math.sqrt(L * L + (S * S - L * L) * (1 - math.exp(-1))), rel_tol=1e-12)
    # 기간이 길수록 장기 쪽으로 — 지금이 조용하면 늘고, 시끄러우면 준다
    a = [vd.term_sigma(L, S, m / 12) for m in vd.FORECAST_MONTHS]
    b = [vd.term_sigma(S, L, m / 12) for m in vd.FORECAST_MONTHS]
    assert a == sorted(a) and b == sorted(b, reverse=True)
    assert vd.term_sigma(L, None, 1.0) == L and vd.term_sigma(None, S, 1.0) == S and vd.term_sigma(None, None, 1) is None


def test_EWMA_는_흔들림의_크기를_따라간다() -> None:
    a = 0.01
    c = [100.0]
    for i in range(300):
        c.append(c[-1] * math.exp(a if i % 2 else -a))
    d = [f"d{i:04d}" for i in range(len(c))]
    v = pt.ewma_vol(d, c)
    assert math.isclose(v["ewma"], a * math.sqrt(252), rel_tol=1e-3) and v["date"] == d[-1]
    # 마지막 60일만 흔들림이 두 배 — 장기 평균보다 지금 쪽(두 배)에 훨씬 가깝다
    for i in range(60):
        c.append(c[-1] * math.exp(2 * a if i % 2 else -2 * a))
    v2 = pt.ewma_vol([f"d{i:04d}" for i in range(len(c))], c)
    assert v2["ewma"] > 1.9 * a * math.sqrt(252)
    assert pt.ewma_vol(d[:50], c[:50]) is None


def test_지금이_조용하면_가까운_범위가_좁다() -> None:
    긴 = vd.forecast(close=100.0, beta=1.0, sigma=0.4, market=시장, rf=0.03)
    짧 = vd.forecast(close=100.0, beta=1.0, sigma=0.4, market=시장, rf=0.03, sigma_short=0.2)
    for h0, h1 in zip(긴["horizons"], 짧["horizons"], strict=True):
        assert h1["high68"] - h1["low68"] < h0["high68"] - h0["low68"]
        assert h1["low68"] < h1["low50"] < h1["expected"] < h1["high50"] < h1["high68"]
        assert math.isclose(h1["sigma"], vd.term_sigma(0.4, 0.2, h1["months"] / 12))
    # 1개월은 거의 지금 변동성, 1년은 장기 쪽
    assert 짧["horizons"][0]["sigma"] < 0.25 and 짧["horizons"][-1]["sigma"] > 0.3
    # 반반 범위 = 같은 로그정규의 25~75% 분위
    h = 긴["horizons"][-1]
    assert math.isclose(1 - vd.prob_above(h["low50"] / 100, 긴["er"], 0.4, 1.0), 0.25, abs_tol=1e-3)
    # 최근 변동성이 없으면 예전과 같다
    assert 긴["sigma_short"] is None and all(h["sigma"] == 0.4 for h in 긴["horizons"])


def test_진단과_사다리가_같은_변동성을_쓴다() -> None:
    o = vd.outlook(close=100.0, close_date="2026-10-07", currency="KRW", momentum={"high_52w_proximity": 0.8},
                   risk={"volatility_ann": 0.4, "beta": 1.0}, band=None, opinions=[], today=date(2026, 10, 8),
                   market=시장, rf=0.03, vol={"ewma": 0.2, "date": "2026-10-02"})  # fmt: skip
    assert any("최근 변동성 20.0%이 장기 40.0%로" in 줄 for 줄 in o["lines"])
    assert any(e["label"] == "최근 변동성(EWMA)" for e in o["evidence"])
    b = vd.build(vd.Inputs(name="가", ticker="1", score={"as_of": "d", "total": 60}, outlook=o))
    고점 = next(it for it in b["outlook"]["ladder"]["items"] if it["kind"] == "high")
    er = o["forecast"]["er"]
    assert math.isclose(고점["touch"]["3"], vd.touch_prob(1.25, er, vd.term_sigma(0.4, 0.2, 0.25), 0.25))


def test_주간_지표_작업이_최근_변동성을_쌓는다(client) -> None:  # noqa: ANN001
    시세넣기(client, 1, 계열(1000, 수익률(700)))
    metrics_job.compute_all(client, [(1, "005930", "KR")], ["5Y"], 지표_기준일)  # type: ignore[arg-type]
    행 = json.loads(client.conn.execute("SELECT stats_json FROM price_patterns").fetchone()[0])
    assert 행["vol"]["ewma"] > 0 and 행["vol"]["date"] <= 지표_기준일
