"""자기 시세 이력의 사실 넷 — 낙폭 회복·최악의 한 달·계절성·신고가 뒤 (docs/analysis.md 31~34장, docs/infra.md 25.1057).

손으로 셀 수 있는 계열로 횟수·거래일·분위를 묶는다. 셈이 하루만 어긋나도 "회복까지 140거래일" 같은 문장이 틀린다.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date

from batch.jobs import metrics as metrics_job
from batch.services import history as hs
from batch.services import patterns as pt
from batch.services import verdict as vd
from tests.test_metrics_beta import client as client  # noqa: F401 — 픽스처
from tests.test_metrics_beta import 계열, 수익률, 시세넣기
from tests.test_metrics_beta import 기준일 as 지표_기준일


def _날(n: int) -> list[str]:
    return [f"d{i:05d}" for i in range(n)]


def test_낙폭_회복은_그_깊이를_지난_날부터_센다() -> None:
    # 100 에서 300일 평평 → 10일에 걸쳐 74 까지(−26%) → 20일 뒤 100 회복 → 다시 −12% 로 끝(진행 중).
    # 하루 −2.6 씩이라 −10%·−20% 문턱에 딱 맞는 날이 없다(부동소수 경계를 피한다)
    c = [100.0] * 300
    c += [100 - 2.6 * (i + 1) for i in range(10)]  # 97.4 94.8 92.2 89.6(303) … 79.2(307) … 74
    c += [74 + 26 * (i + 1) / 20 for i in range(20)]  # 100 으로
    c += [101.0] * 30 + [88.88] * 5
    d = hs.drawdowns(_날(len(c)), c)
    lv10, lv20, lv30 = (d["levels"][f"{x:.2f}"] for x in hs.DD_LEVELS)
    # −10% 는 89.6(인덱스 303)에서, −20% 는 79.2(307)에서 처음 지났다. 회복은 100 이 되는 날 329
    assert lv10["n"] == 2 and lv10["recovered"] == 1 and lv10["open"] == 1 and lv10["median_days"] == 329 - 303
    assert lv20 == {"n": 1, "recovered": 1, "open": 0, "median_days": 329 - 307}
    assert lv30["n"] == 0 and lv30["median_days"] is None
    assert math.isclose(d["now"], 88.88 / 101 - 1, abs_tol=1e-4) and d["days_since_peak"] == 5
    assert d["worst"][0]["depth"] == -0.26 and d["worst"][0]["recovered"] == "d00329" and d["worst"][0]["days"] == 329 - 299
    assert hs.drawdowns(_날(100), c[:100]) is None


def test_최악의_한_달은_실제_분포의_꼬리() -> None:
    # 하루 수익률: 나머지는 +0.1%, 20일에 한 번 −5% (정규분포가 아님)
    c = [100.0]
    for i in range(999):
        c.append(c[-1] * (0.95 if i % 20 == 0 else 1.001))
    t = hs.tail(_날(len(c)), c)
    # 하루: 5% 분위가 정확히 −5% 무리 안 — 가장 나쁜 5% 의 평균도 −5%
    assert math.isclose(t["d1"]["cvar"], -0.05, abs_tol=1e-4) and math.isclose(t["d1"]["worst"], -0.05, abs_tol=1e-6)
    # 21일 창에는 −5% 가 한 번 또는 두 번 — 가장 나빴던 한 달은 두 번 든 창
    assert math.isclose(t["m1"]["worst"], 0.95**2 * 1.001**19 - 1, abs_tol=1e-4)
    assert t["m1"]["cvar"] <= t["m1"]["var"] < 0 and t["m1"]["n"] == len(c) - hs.MONTH_DAYS
    assert hs.tail(_날(100), c[:100]) is None


def test_계절성은_달력의_달로_모은다() -> None:
    import datetime as dt

    d0 = dt.date(2021, 1, 1)
    날, 값 = [], []
    x = 100.0
    for i in range(365 * 4):
        d = d0 + dt.timedelta(days=i)
        x *= 1.002 if d.month == 3 else 0.9999  # 3월만 오른다
        날.append(d.isoformat())
        값.append(x)
    s = hs.season(날, 값)
    assert s["months"]["03"]["up"] == s["months"]["03"]["n"] >= hs.SEASON_MIN
    assert s["months"]["07"]["up"] == 0 and s["months"]["03"]["avg"] > 0.05
    줄 = hs.lines({"season": s}, None, 2)  # 이번 달(2월)과 다음 달(3월)
    m2, m3 = s["months"]["02"], s["months"]["03"]
    assert 줄 == [f"계절성(우연이 큼 — 판정에 쓰지 않음): 2월 {m2['n']}번 중 0번 오름·평균 {m2['avg'] * 100:+.1f}% · "
                 f"3월 {m3['n']}번 중 {m3['n']}번 오름·평균 {m3['avg'] * 100:+.1f}%"]
    assert hs.lines({"season": s}, None, 12)[0].startswith("계절성(우연이 큼 — 판정에 쓰지 않음): 12월")  # 12월 다음은 1월
    assert hs.season(날[:300], 값[:300]) is None


def test_신고가_사건은_앞_252일을_넘은_날_식힘_뒤만() -> None:
    # 300일 평평 뒤 하루씩 오르기 → 첫 신고가는 300, 그다음은 300 + 22 (식힘 21일 뒤), …
    c = [100.0] * 300 + [100 * 1.001 ** (i + 1) for i in range(400)]
    bo, raw = hs.breakout(_날(len(c)), c)
    사건 = list(range(300, len(c), hs.BREAKOUT_COOLDOWN + 1))
    assert bo["events"] == len(사건) and bo["last"] == f"d{사건[-1]:05d}"
    뒤 = [i for i in 사건 if i + pt.HORIZON_DAYS[1] < len(c)]
    assert bo["h"]["1"]["n"] == len(뒤) == len(raw["1"]) and math.isclose(bo["h"]["1"]["median"], 1.001**21 - 1, abs_tol=1e-4)
    시장 = hs.market_breakout([raw, raw])
    assert 시장["1"]["n"] == 2 * len(뒤)


def test_진단_줄과_주간_작업(client) -> None:  # noqa: ANN001
    random.seed(2)
    c = [100.0]
    for _ in range(899):
        c.append(c[-1] * (1 + random.gauss(0.0004, 0.02)))
    h = {"drawdown": hs.drawdowns(_날(900), c), "tail": hs.tail(_날(900), c), "breakout": hs.breakout(_날(900), c)[0]}
    o = vd.outlook(close=c[-1], close_date="d", currency="KRW", momentum=None, risk=None, band=None, opinions=[],
                   today=date(2026, 10, 8), history=h, market_breakout={"1": pt.dist([0.01, 0.02, -0.01])})  # fmt: skip
    assert any(x.startswith("낙폭 회복(") for x in o["lines"]) and any(x.startswith("최악의 한 달(") for x in o["lines"])
    assert any(x.startswith("52주 신고가 뒤(") and "시장 전체 신고가 뒤" in x for x in o["lines"])
    assert o["history"]["market_breakout"]["1"]["n"] == 3
    # 주간 작업이 쌓는다
    시세넣기(client, 1, 계열(1000, 수익률(700)))
    metrics_job.compute_all(client, [(1, "005930", "KR")], ["5Y"], 지표_기준일)  # type: ignore[arg-type]
    행 = json.loads(client.conn.execute("SELECT stats_json FROM price_patterns").fetchone()[0])
    assert 행["drawdown"]["levels"] and 행["tail"]["m1"]["n"] > 0 and "season" in 행 and "breakout" in 행
    설정 = client.conn.execute("SELECT value FROM settings WHERE key = ?", [hs.market_key("KR")]).fetchone()
    assert 설정 is None or "h" in json.loads(설정[0])


def test_신고가_줄은_견줄_것이_없으면_괄호를_비우지_않는다() -> None:
    bo = {"events": 2, "h": {"1": pt.dist([0.1, 0.2])}, "base": {}}
    assert hs.lines({"breakout": bo}, None, 1) == ["52주 신고가 뒤(지난 2번): 1개월 중앙값 +15.0%·오른 비율 100%"]
    줄 = hs.lines({"breakout": bo}, {"1": pt.dist([-0.1])}, 1)[0]
    assert 줄.endswith("(시장 전체 신고가 뒤 -10.0%·0%)")
