"""실제 수익률로 그린 경로·움직임 분해·매물대 (docs/analysis.md 43~45장, docs/infra.md 25.1064).

- 부트스트랩이 정규 분위를 그대로 되살리면 예상 주가의 범위와 **같아야** 한다 — 중심을 옮기는 식이 틀리면 다르다
- 정규 가정은 이 종목이 해본 적 없는 움직임에도 확률을 준다 — 실제 경로는 그러지 않는다
- 분해의 세 몫은 더하면 종목 수익이 된다. 매물대는 거래대금이 몰린 가격대를 비율로 적는다
"""

from __future__ import annotations

import json
import math
import random
from datetime import date

from batch.jobs import metrics as metrics_job
from batch.services import history as hs
from batch.services import simulation as sim
from batch.services import verdict as vd
from tests.test_metrics_beta import client as client  # noqa: F401 — 픽스처
from tests.test_metrics_beta import 계열, 수익률, 시세넣기
from tests.test_metrics_beta import 기준일 as 지표_기준일

시장 = {"annual": 0.08, "years": 10, "since": "a", "until": "b"}


def _계열(n: int = 1300, seed: int = 4, drift: float = 0.0) -> list[float]:
    random.seed(seed)
    c = [100.0]
    for _ in range(n - 1):
        c.append(c[-1] * math.exp(drift + random.gauss(0, 0.02)))
    return c


def test_부트스트랩은_같은_씨앗이면_같고_표류를_뺀다() -> None:
    c = _계열(drift=0.002)  # 하루 +0.2% 씩 오르는 5년 — 그대로 되풀이하면 1년 +65%
    a, b = sim.bootstrap(c, seed=7), sim.bootstrap(c, seed=7)
    assert a == b and a["days_used"] == min(len(c) - 1, sim.BOOT_DAYS)  # 최근 5년만
    중앙 = a["terminal"]["252"][a["q"].index(0.5)]
    assert abs(중앙) < 0.05  # 표류를 뺐다 — 중심은 일일 의견이 CAPM 으로 옮긴다
    for qs in a["terminal"].values():
        assert qs == sorted(qs)
    assert sim.bootstrap(c[:200], seed=1) is None


def test_정규_가정은_해본_적_없는_움직임에도_확률을_준다() -> None:
    # 하루 ±1% 를 번갈아 — 열흘 묶음을 이어도 경로가 지금에서 거의 벗어나지 않는다
    c = [100.0]
    for i in range(1000):
        c.append(c[-1] * math.exp(0.01 if i % 2 else -0.01))
    b = sim.bootstrap(c, seed=3)
    assert sim.touch(b, 1.5, 252) == 0.0 and sim.touch(b, 0.7, 252) == 0.0
    er0 = math.exp(b["sigma"] ** 2 / 2) - 1
    assert vd.touch_prob(1.5, er0, b["sigma"], 1.0) > 0.01  # 같은 변동성의 정규 가정은 닿는다고 본다
    # 도달 확률은 멀수록 작다
    b2 = sim.bootstrap(_계열(), seed=3)
    assert sim.touch(b2, 1.1, 252) >= sim.touch(b2, 1.3, 252) >= sim.touch(b2, 1.6, 252)
    assert sim.touch(b2, 1.0, 252) is None
    # "한 번이라도 닿음" 은 경로의 최고로 센다 — 끝값으로 세면 반사 원리의 약 두 배가 사라진다
    끝95 = b2["terminal"]["252"][b2["q"].index(0.95)]
    assert sim.touch(b2, math.exp(끝95), 252) > 0.08


def test_정규_분위를_되살리면_예상_주가_범위와_같다() -> None:
    """중심을 옮기는 식((ln(1+E[r]) − σ²/2)·t)을 묶는다 — 끝 분포가 정규 분위면 두 범위가 같아야 한다."""
    f = vd.forecast(close=100.0, beta=1.0, sigma=0.3, market=시장, rf=0.03)
    z = {0.05: -vd.FORECAST_Z["90"], 0.16: -vd.FORECAST_Z["68"], 0.25: -vd.FORECAST_Z["50"], 0.5: 0.0,
         0.75: vd.FORECAST_Z["50"], 0.84: vd.FORECAST_Z["68"], 0.95: vd.FORECAST_Z["90"]}  # fmt: skip
    q = list(sim.BOOT_Q)
    boot = {"q": q, "days_used": 1000, "terminal": {str(d): [z[p] * 0.3 * math.sqrt(m / 12) for p in q]
                                                     for m, d in vd.BOOT_DAYS_OF.items()}}  # fmt: skip
    줄 = vd._boot_ranges(f, boot, 100.0, "KRW")
    for h in f["horizons"]:
        for k in ("low90", "low68", "low50", "high50", "high68", "high90"):
            assert math.isclose(h["boot"][k], h[k], rel_tol=1e-9), (h["months"], k)
    assert 줄.startswith("실제 수익률로 그린 1년 범위(")


def test_사다리에_실제_꼬리와_매물대가_실린다() -> None:
    c = _계열()
    h = {"boot": sim.bootstrap(c, seed=1),
         "profile": {"bins": [{"lo": 0.8, "hi": 0.9, "share": 0.4}, {"lo": 1.1, "hi": 1.2, "share": 0.3}],
                     "top": [0, 1], "now": 1}}  # fmt: skip
    o = vd.outlook(close=100.0, close_date="d", currency="KRW", momentum={"high_52w_proximity": 0.8},
                   risk={"volatility_ann": 0.3, "beta": 1.0}, band=None, opinions=[], today=date(2026, 10, 8),
                   market=시장, rf=0.03, history=h)  # fmt: skip
    lad = vd.ladder(close=100.0, currency="KRW", outlook=o, signals=[], checks=[])
    매물 = [it for it in lad["items"] if it["kind"] == "profile"]
    assert [round(it["price"], 6) for it in 매물] == [115.0, 85.0] and 매물[1]["label"].startswith("매물대 1위(지난 1년 거래대금 40%)")
    고점 = next(it for it in lad["items"] if it["kind"] == "high")
    assert 고점["touch_boot"]["12"] == sim.touch(h["boot"], 1.25, 252) and "touch_norm0" in 고점


def test_매물대는_거래대금이_몰린_가격대() -> None:
    날 = [f"d{i:04d}" for i in range(300)]
    c = [100.0] * 100 + [120.0] * 200  # 날 수는 120 이 많지만 거래대금은 100 에 몰렸다
    v = [1e9] * 100 + [1e8] * 200
    p = hs.volume_profile(날, c, v)
    top = p["bins"][p["top"][0]]
    assert top["lo"] <= 100 / 120 <= top["hi"] and p["now"] == hs.PROFILE_BINS - 1
    assert math.isclose(sum(b["share"] for b in p["bins"]), 1.0, abs_tol=1e-3)
    assert hs.volume_profile(날[:50], c[:50], v[:50]) is None


def test_움직임_분해는_더하면_종목_수익() -> None:
    날 = [f"d{i:04d}" for i in range(300)]
    random.seed(9)
    ix, c, 지수 = 1000.0, [100.0], {날[0]: 1000.0}
    for d in 날[1:]:
        m = random.gauss(0, 0.01)
        ix *= 1 + m
        지수[d] = ix
        c.append(c[-1] * (1 + 2 * m))
    mi = hs.move_inputs(날, c, 지수)
    assert math.isclose(mi["beta"], 2.0, rel_tol=1e-6)
    mine = {"until": "u", "beta": 1.5, "w": {"20": {"since": "s", "stock": 0.25, "market": 0.1}}}
    peer = {"w": {"20": {"since": "s", "stock": 0.18, "market": 0.1}}}
    m = hs.move_parts(mine, [peer] * 3)["w"]["20"]
    assert (m["market"], m["sector"], m["own"]) == (0.15, 0.08, 0.02)
    assert math.isclose(m["market"] + m["sector"] + m["own"], 0.25)
    # 같은 업종이 모자라면 업종 몫 없이 나머지를 이 종목 몫으로
    m2 = hs.move_parts(mine, [peer] * (hs.MOVE_MIN_PEERS - 1))["w"]["20"]
    assert m2["sector"] is None and m2["own"] == 0.1
    # 창의 시작일이 다른 종목(상장·거래정지)은 같은 업종 평균에 넣지 않는다
    다른날 = {"w": {"20": {"since": "x", "stock": 9.0, "market": 0.1}}}
    assert hs.move_parts(mine, [peer] * 3 + [다른날])["w"]["20"]["peers"] == 3
    assert any(x.startswith("움직임 분해(지난 20거래일") for x in hs.move_lines(hs.move_parts(mine, [peer] * 3)))


def test_주간_작업이_쌓는다(client) -> None:  # noqa: ANN001
    시세넣기(client, 1, 계열(1000, 수익률(1300)))
    client.conn.execute("UPDATE prices SET volume = 1000")
    metrics_job.compute_all(client, [(1, "005930", "KR")], ["5Y"], 지표_기준일)  # type: ignore[arg-type]
    행 = json.loads(client.conn.execute("SELECT stats_json FROM price_patterns").fetchone()[0])
    assert 행["boot"]["terminal"]["252"] and 행["profile"]["top"] and "moves" in 행
