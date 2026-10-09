"""시장 시나리오·같은 업종 대체 후보 (docs/analysis.md 46·47장, docs/infra.md 25.1067·25.1068).

시나리오는 종목과 지수를 **같은 날** 짝으로 뽑아야 상관이 남는다 — 따로 뽑으면 시장이 빠진 경로에서도 종목이 평균 0 이 된다.
대체 후보는 "점수 같거나 높음 + 변동성 낮음" 의 두 비교만 쓴다(새 문턱 없음).
"""

from __future__ import annotations

import json
import math
import random
from datetime import date

from batch.core import db
from batch.jobs import verdicts as job
from batch.services import insights as ins
from batch.services import simulation as sim
from tests.test_forecast_track_1037 import _시장
from tests.test_portfolio_job import MemClient


def _쌍(beta: float, n: int = 1300, seed: int = 2) -> tuple[list[str], list[float], dict[str, float]]:
    random.seed(seed)
    날 = [f"d{i:05d}" for i in range(n)]
    c, ix, 지수 = [100.0], 1000.0, {날[0]: 1000.0}
    for d in 날[1:]:
        m = random.gauss(0, 0.012)
        ix *= math.exp(m)
        지수[d] = ix
        c.append(c[-1] * math.exp(beta * m + random.gauss(0, 0.005)))
    return 날, c, 지수


def test_시장이_빠진_경로에서_베타만큼_빠진다() -> None:
    날, c, 지수 = _쌍(2.0)
    s = sim.scenarios(날, c, 지수, seed=5)
    assert math.isclose(s["beta"], 2.0, rel_tol=0.05)
    assert s["down"]["n"] >= sim.SCENARIO_MIN_PATHS and s["up"]["n"] >= sim.SCENARIO_MIN_PATHS
    # 지수 중앙값이 −20% 아래, 종목 중앙값은 그보다 더 깊다(베타 2)
    assert s["down"]["index"] <= -sim.SCENARIO_MOVE and s["down"]["stock"][1] < s["down"]["index"] * 1.5
    assert s["up"]["stock"][1] > s["up"]["index"]
    assert s["down"]["stock"] == sorted(s["down"]["stock"])
    # 같은 씨앗이면 같은 결과
    assert sim.scenarios(날, c, 지수, seed=5) == s
    assert "시장이 1년에 20% 넘게 빠진 경로" in sim.scenario_line(s)


def test_따로_움직이는_종목은_시장과_무관() -> None:
    날, c, 지수 = _쌍(0.0)
    s = sim.scenarios(날, c, 지수, seed=5)
    assert abs(s["beta"]) < 0.1 and abs(s["down"]["stock"][1]) < 0.05
    assert sim.scenarios(날[:100], c[:100], 지수, seed=1) is None


def _m(sid: int, total: float | None, vol: float | None) -> dict:
    return {"stock_id": sid, "ticker": str(sid), "name": f"종목{sid}", "total": total, "vol": vol, "mdd": -0.3}


def test_대체_후보는_점수_같거나_높고_변동성_낮은_순() -> None:
    members = [_m(1, 60, 0.40), _m(2, 60, 0.30), _m(3, 70, 0.20), _m(4, 59.9, 0.10), _m(5, 80, 0.45),
               _m(6, 65, 0.35), _m(7, 61, None), _m(8, 90, 0.25)]  # fmt: skip
    a = ins.alternatives(1, members)
    # 4(점수 낮음)·5(변동성 높음)·7(변동성 없음)은 빠진다. 변동성 낮은 순 3개
    assert [x["stock_id"] for x in a] == [3, 8, 2] and a[0]["my_vol"] == 0.40
    assert ins.alternatives(1, [_m(1, None, 0.4), _m(2, 90, 0.1)]) == []


def test_일일_의견에_실린다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    _시장(c, 기준가=100.0, 지금가=104.0)
    c.execute("UPDATE stocks SET sector = '반도체' WHERE id = 1")
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, sector, source, fetched_at)"
              " VALUES (2, '000660', 'KOSPI', 'KR', '나', 'KRW', '반도체', 't', 't')")  # fmt: skip
    c.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
              " weights_json, rank_in_market, calc_version, created_at) VALUES (2, '2026-10-07', 70, '{}', 0, '{}', 1,"
              " 10, 't')")  # fmt: skip
    for sid, v in ((1, 0.4), (2, 0.2)):
        c.execute("INSERT INTO performance_metrics (stock_id, as_of_date, window, volatility_ann, mdd, data_points,"
                  " calc_version, created_at) VALUES (?, '2026-10-06', '3Y', ?, -0.3, 700, 1, 't')", [sid, v])  # fmt: skip
    mem.batch(job.build_market(mem, "KR", date(2026, 10, 8), []))  # type: ignore[arg-type]
    d = json.loads(c.execute("SELECT detail_json FROM stock_verdicts WHERE stock_id = 1").fetchone()[0])
    assert [a["stock_id"] for a in d["alternatives"]] == [2]
    assert any(r.startswith("같은 업종에서 점수가 같거나 높고 변동성이 낮은 종목: 나(점수 70·변동성 20%)") for r in d["reasons"])
