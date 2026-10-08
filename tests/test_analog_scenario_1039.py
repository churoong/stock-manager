"""비슷한 국면·1년 시나리오 (docs/analysis.md 13·14장, docs/infra.md 25.1039·25.1040).

비슷한 국면은 주간 작업이 칸을 만들고 일일 의견이 **모멘텀 원값으로** 오늘 칸을 고른다 — 두 쪽의 상태 정의가 어긋나면
엉뚱한 칸의 분포를 "지금과 같았던 날" 이라 부른다. 그래서 같은 계열에서 두 정의가 같은 칸을 고르는지를 묶는다.
"""

from __future__ import annotations

import math
import random
from datetime import date

from batch.jobs import metrics as metrics_job
from batch.services import forecast_track as ft
from batch.services import patterns as pt
from batch.services import scoring
from batch.services import verdict as vd
from tests.test_metrics_beta import client as client  # noqa: F401 — 픽스처
from tests.test_metrics_beta import 계열, 수익률, 시세넣기
from tests.test_metrics_beta import 기준일 as 지표_기준일


def _계열(n: int = 900, seed: int = 7) -> tuple[list[str], list[float]]:
    random.seed(seed)
    c = [100.0]
    for _ in range(n - 1):
        c.append(c[-1] * (1 + random.gauss(0.0003, 0.02)))
    return [f"d{i:05d}" for i in range(n)], c


def test_오늘_칸은_모멘텀_원값과_같은_정의() -> None:
    d, c = _계열()
    t = pt.table(d, c)
    assert t is not None
    r3 = scoring.momentum_metrics(c)["momentum_3m"]
    prox = scoring.high_52w_proximity(c)
    assert math.isclose(t["state"]["r3"], r3, abs_tol=1e-6) and math.isclose(t["state"]["prox"], prox, abs_tol=1e-6)
    assert pt.bucket(r3, prox, t["edges"]) == t["current"]


def test_고점은_252거래일_창() -> None:
    """230거래일 전 고점이 창 안에 들어야 한다 — 창이 짧으면 52주 고점 근접이 1 이 된다."""
    d, c = _계열(900)
    c = c[:]
    c[-230] = max(c) * 2
    t = pt.table(d, c)
    assert math.isclose(t["state"]["prox"], c[-1] / c[-230], abs_tol=1e-6)
    assert math.isclose(t["state"]["prox"], scoring.high_52w_proximity(c), abs_tol=1e-6)


def test_앞으로_수익은_정확히_그_거래일_뒤() -> None:
    """매일 같은 비율로 오르면 모든 칸·모든 날의 n 거래일 뒤 수익이 같다."""
    c = [100 * 1.001**i for i in range(700)]
    t = pt.table([f"d{i}" for i in range(700)], c)
    for 칸 in t["buckets"].values():
        for m, dd in 칸["h"].items():
            assert math.isclose(dd["median"], 1.001 ** pt.HORIZON_DAYS[int(m)] - 1, abs_tol=1e-4)
            assert dd["up"] == 1.0


def test_표본이_모자라면_칸을_비우고_그렇게_말한다() -> None:
    assert pt.table(*_계열(300)) is None  # 상태를 낼 날(252일 이후)이 모자람
    t = pt.table(*_계열())
    빈칸 = next(f"{i}-{j}" for i in range(3) for j in range(3) if f"{i}-{j}" not in t["buckets"]) if len(
        t["buckets"]) < 9 else None  # fmt: skip
    if 빈칸:
        i, j = (int(x) for x in 빈칸.split("-"))
        r3 = [t["edges"]["r3"][0] - 1, t["edges"]["r3"][0] + 1e-9, t["edges"]["r3"][1] + 1][i]
        prox = [t["edges"]["prox"][0] - 0.1, t["edges"]["prox"][0] + 1e-9, t["edges"]["prox"][1] + 0.01][j]
        assert pt.pick(t, r3, prox)["empty"] is True
    assert pt.pick(t, None, 0.9) is None


def test_진단에_실리고_성적표에_모델로_쌓인다() -> None:
    d, c = _계열()
    t = pt.table(d, c)
    a = pt.pick(t, scoring.momentum_metrics(c)["momentum_3m"], scoring.high_52w_proximity(c))
    o = vd.outlook(close=c[-1], close_date="2026-10-07", currency="KRW", momentum=None, risk=None, band=None,
                   opinions=[], today=date(2026, 10, 8), analog=a)  # fmt: skip
    assert o["analog"]["label"] == pt.label(t["current"])
    assert any(줄.startswith("비슷한 국면(") for 줄 in o["lines"])
    m = ft.log_models(o)
    assert m["analog"]["3"]["exp"] == next(h["median"] for h in a["horizons"] if h["months"] == 3)


def test_주간_지표_작업이_국면표를_쌓는다(client) -> None:  # noqa: ANN001
    시세넣기(client, 1, 계열(1000, 수익률(700)))
    metrics_job.compute_all(client, [(1, "005930", "KR")], ["5Y"], 지표_기준일)  # type: ignore[arg-type]
    행 = client.conn.execute("SELECT stock_id, stats_json FROM price_patterns").fetchone()
    assert 행 is not None and 행[0] == 1 and '"buckets"' in 행[1]


def test_시나리오는_순자산_성장_곱하기_밴드_분위() -> None:
    band = {"pbr": 0.8, "prices": {"p20": 70.0, "p50": 100.0, "p80": 140.0}}
    s = vd.scenario(close=90.0, band=band, fundamentals={"roe": 0.12, "dividend_yield": 0.02, "bp": 1.25})
    # 배당 몫 = 0.02 / 1.25 = 0.016 → 성장 0.104
    assert math.isclose(s["growth"], 0.104)
    assert math.isclose(s["base"], 100 * 1.104) and math.isclose(s["bear"], 70 * 1.104)
    assert math.isclose(s["hold"], 90 * 1.104)
    assert vd.scenario(close=90.0, band=band, fundamentals={"roe": None}) is None
    o = vd.outlook(close=90.0, close_date="d", currency="KRW", momentum=None, risk=None,
                   band={"current_value": 0.8, "band_close": 90.0, "p20": 0.56, "p50": 0.8, "p80": 1.12},
                   opinions=[], today=date(2026, 10, 8), fundamentals={"roe": 0.10})  # fmt: skip
    assert math.isclose(o["scenario"]["base"], 90 * 1.10) and math.isclose(ft.log_models(o)["scenario"]["12"]["exp"], 0.10)
