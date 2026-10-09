"""함께 움직이는 종목·변동성 국면·감성과 가격의 엇갈림 (docs/analysis.md 50~52장, docs/infra.md 25.1070·25.1071).

- 함께 움직이는 종목은 **시장 몫을 뺀** 상관이어야 한다 — 빼지 않으면 베타가 큰 종목끼리 짝이 되어 "숨은 테마" 가 아니라
  "시장과 같이 움직인다" 를 말한다
- 변동성 국면은 자기 이력 삼분위, 엇갈림은 부호가 반대일 때만
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta

from batch.core import db
from batch.jobs import verdicts as job
from batch.services import history as hs
from batch.services import insights as ins
from tests.test_forecast_track_1037 import _시장
from tests.test_portfolio_job import MemClient


def test_함께_움직이는_종목은_시장_몫을_뺀_상관() -> None:
    random.seed(4)
    날 = [f"d{i:04d}" for i in range(300)]
    시장 = {d: random.gauss(0, 0.01) for d in 날}
    테마 = {d: random.gauss(0, 0.01) for d in 날}
    계열: dict[int, dict[str, float]] = {}
    for sid in range(1, 11):
        계열[sid] = {}
        for d in 날:
            beta = 3.0 if sid in (3, 4) else 1.0  # 3·4번은 시장에만 크게 흔들린다
            x = beta * 시장[d] + random.gauss(0, 0.01)
            if sid in (1, 2):
                x += 테마[d]  # 1·2번은 업종이 달라도 같은 재료로 움직인다
            계열[sid][d] = x
    업종 = {1: "반도체", 2: "화학", 3: "반도체", 4: "반도체"}
    out = hs.comovers(계열, 시장, 업종)
    assert out[1][0]["stock_id"] == 2 and out[1][0]["corr"] > 0.3 and out[1][0]["same_sector"] is False
    # 시장 몫을 빼서 3·4번(고베타끼리)은 서로 짝이 아니다
    assert out[3][0]["corr"] < 0.2
    assert len(out[1]) == hs.COMOVE_TOP
    assert hs.comovers({1: {d: 0.01 for d in 날[:100]}}, {d: 0.0 for d in 날[:100]}, {}) == {}


def test_변동성_국면은_자기_이력_삼분위() -> None:
    random.seed(7)
    c = [100.0]
    for i in range(799):
        s = 0.005 if i < 400 else 0.03  # 앞은 조용, 뒤는 시끄럽다
        c.append(c[-1] * math.exp(random.gauss(0, s)))
    v = hs.vol_regime([f"d{i:04d}" for i in range(800)], c)
    assert v["bucket"] == 2 and v["pct"] > 0.66 and v["now"] > v["edges"][1]
    assert v["buckets"]["0"]["1"]["n"] >= 30
    assert hs.vol_regime_line(v).startswith("변동성 국면: 지금 한 달 변동성")
    assert hs.vol_regime(["d"] * 30, c[:30]) is None


def test_엇갈림은_부호가_반대일_때만() -> None:
    g = ins.sentiment_gap({"as_of_date": "2026-10-07", "sentiment": 40, "delta_30d": 25.0}, 100.0, 90.0)
    assert g["opposite"] and g["price_ret"] == -0.1
    assert "좋아졌는데 주가는 빠졌다" in ins.sentiment_gap_line(g)
    같은쪽 = ins.sentiment_gap({"as_of_date": "d", "sentiment": 1, "delta_30d": 25.0}, 100.0, 110.0)
    assert not 같은쪽["opposite"] and ins.sentiment_gap_line(같은쪽) is None
    assert ins.sentiment_gap({"delta_30d": None}, 100.0, 90.0) is None
    r = ins.radar([{"stock_id": 1, "ticker": "a", "name": "가", "sentiment_gap": g},
                   {"stock_id": 2, "ticker": "b", "name": "나", "sentiment_gap": 같은쪽}])  # fmt: skip
    assert [x["stock_id"] for x in r["gap"]] == [1]


def test_일일_의견이_엇갈림을_싣는다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    _시장(c, 기준가=100.0, 지금가=104.0)  # 09-07 100, 10-07 104
    c.execute("INSERT INTO sentiment_scores (stock_id, as_of_date, sentiment, article_count, positive_count,"
              " negative_count, negative_count_7d, decay_halflife_days, delta_30d, method, calc_version, created_at)"
              " VALUES (1, '2026-10-07', -30, 10, 2, 8, 3, 7, -40, 'vader', 1, 't')")  # fmt: skip
    mem.batch(job.build_market(mem, "KR", date(2026, 10, 8), []))  # type: ignore[arg-type]
    d = json.loads(c.execute("SELECT detail_json FROM stock_verdicts").fetchone()[0])
    g = d["sentiment_gap"]
    assert g["opposite"] and math.isclose(g["price_ret"], 0.04) and g["sent_delta"] == -40
    assert any(x.startswith("감성과 가격의 엇갈림(2026-10-07까지 30일)") for x in d["reasons"])
    레이더 = json.loads(c.execute("SELECT value FROM settings WHERE key = ?", [job.radar_key("KR")]).fetchone()[0])
    assert 레이더["gap"][0]["stock_id"] == 1
    assert (date(2026, 10, 7) - timedelta(days=ins.SENT_GAP_DAYS)).isoformat() == "2026-09-07"


def test_주간_작업이_쌓는다(client) -> None:  # noqa: ANN001
    from batch.jobs import metrics as metrics_job
    from tests.test_metrics_beta import 계열, 수익률, 시세넣기
    from tests.test_metrics_beta import 기준일 as 지표_기준일

    시세넣기(client, 1, 계열(1000, 수익률(800)))
    metrics_job.compute_all(client, [(1, "005930", "KR")], ["5Y"], 지표_기준일)  # type: ignore[arg-type]
    행 = json.loads(client.conn.execute("SELECT stats_json FROM price_patterns").fetchone()[0])
    assert 행["vol_regime"]["buckets"] and "comovers" in 행  # 한 종목뿐이라 짝은 없다(None)


from tests.test_metrics_beta import client as client  # noqa: E402,F401 — 픽스처
