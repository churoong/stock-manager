"""시장 국면까지 맞춘 비슷한 국면 (docs/analysis.md 29장, docs/infra.md 25.1054).

주간 작업이 지난날의 시장 국면으로 칸을 나누고, 일일 의견이 **오늘의** 시장 국면을 추세 필터 함수로 고른다 —
두 정의가 어긋나면 "약세였던 날" 이라 부른 날이 사실 강세였던 날이다. 그래서 두 정의가 날마다 같은지,
나눈 칸이 정말 그 국면의 날만 담는지, 일일 의견이 오늘 국면의 칸을 싣는지를 묶는다.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta

from batch.core import db
from batch.jobs import verdicts as job
from batch.services import patterns as pt
from batch.services import scoring, trend
from batch.services import verdict as vd
from tests.test_portfolio_job import MemClient


def _지수(n: int = 400, seed: int = 3) -> dict[str, float]:
    random.seed(seed)
    d0 = date(2025, 1, 1)
    out, x = {}, 1000.0
    for i in range(n):
        x *= 1 + random.gauss(0.0, 0.012)
        out[(d0 + timedelta(days=i)).isoformat()] = x
    return out


def test_지난날_국면은_추세_필터와_같은_정의() -> None:
    지수 = _지수()
    g = pt.index_regimes(지수)
    쌍 = sorted(지수.items())
    assert len(g) == len(쌍) - pt.REGIME_SMA_DAYS + 1 and pt.REGIME_SMA_DAYS == trend.SMA_DAYS
    for d, _ in 쌍[pt.REGIME_SMA_DAYS - 1 :: 7]:
        assert g[d] == trend.regime_from_closes("KOSPI", 쌍, d).state
    assert {"bull", "bear"} <= set(g.values())  # 두 국면이 다 나와야 견줄 뜻이 있다


def _계열(n: int = 900, seed: int = 7) -> tuple[list[str], list[float]]:
    random.seed(seed)
    c = [100.0]
    for _ in range(n - 1):
        c.append(c[-1] * (1 + random.gauss(0.0003, 0.02)))
    return [f"d{i:05d}" for i in range(n)], c


def test_나눈_칸은_그_국면의_날만_담는다() -> None:
    """3개월 뒤 올랐던 날을 '강세' 로 적은 국면(테스트용 미래 보기) — 강세 칸은 오른 비율 100%, 약세 칸은 0%."""
    d, c = _계열()
    h = pt.HORIZON_DAYS[3]
    g = {d[i]: ("bull" if c[i + h] > c[i] else "bear") for i in range(len(c) - h)}
    t = pt.table(d, c, g)
    assert t is not None and set(t["by_regime"]) == {"bull", "bear"}
    for k, 칸 in t["by_regime"]["bull"].items():
        assert 칸["h"]["3"]["up"] == 1.0 and 칸["days"] <= t["buckets"][k]["days"]
    for 칸 in t["by_regime"]["bear"].values():
        assert 칸["h"]["3"]["up"] == 0.0
    # 오늘 칸을 고르면 같은 칸 안에서 그 국면의 분포가 붙는다
    r3, prox = scoring.momentum_metrics(c)["momentum_3m"], scoring.high_52w_proximity(c)
    a = pt.pick(t, r3, prox, "bull")
    assert a["regime"]["state"] == "bull" and a["regime"]["horizons"][1]["months"] == 3
    assert a["regime"]["days"] == t["by_regime"]["bull"][a["key"]]["days"]
    # 국면을 모르면(지수 부족) 붙이지 않는다. 표에 나눈 칸이 없으면(참고 분석) 붙이지 않는다
    assert "regime" not in pt.pick(t, r3, prox, None)
    assert "regime" not in pt.pick(pt.table(d, c), r3, prox, "bull")
    # 표본이 모자란 국면은 비었다고 말한다
    t2 = pt.table(d, c, {x: "bear" for x in d[:300]} | {x: "bull" for x in d[300:]})
    assert pt.pick(t2, r3, prox, "bear")["regime"].get("empty") is True


def test_진단_줄은_칸_전체와_나란히() -> None:
    d, c = _계열()
    t = pt.table(d, c, {x: "bear" for x in d})
    a = pt.pick(t, scoring.momentum_metrics(c)["momentum_3m"], scoring.high_52w_proximity(c), "bear")
    o = vd.outlook(close=c[-1], close_date="d", currency="KRW", momentum=None, risk=None, band=None, opinions=[],
                   today=date(2026, 10, 8), analog=a)  # fmt: skip
    줄 = next(x for x in o["lines"] if x.startswith("시장 국면까지 맞추면("))
    assert "지수 200일선 아래" in 줄 and "칸 전체" in 줄
    # 모든 날이 약세면 나눈 칸 = 칸 전체
    assert a["regime"]["horizons"][1]["median"] == a["horizons"][1]["median"]


def test_일일_의견이_오늘_시장_국면의_칸을_싣는다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, source, fetched_at)"
              " VALUES (1, '005930', 'KOSPI', 'KR', '가', 'KRW', 't', 't')")  # fmt: skip
    c.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
              " weights_json, rank_in_market, calc_version, created_at) VALUES (1, '2026-10-07', 60, '{}', 0, '{}', 1,"
              " 10, 't')")  # fmt: skip
    c.execute("INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
              " VALUES (1, '2026-10-07', 100, 100, 'KRW', 't', 't')")  # fmt: skip
    # 지수가 꾸준히 내려 오늘은 200일선 아래 — 약세
    d0 = date(2026, 10, 7)
    for i in range(220):
        c.execute("INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES ('KOSPI', ?, ?, 't', 't')",
                  [(d0 - timedelta(days=i)).isoformat(), 2000.0 + i])  # fmt: skip
    d, closes = _계열()
    t = pt.table(d, closes, {x: ("bear" if i % 2 else "bull") for i, x in enumerate(d)})
    c.execute("INSERT INTO price_patterns (stock_id, as_of_date, stats_json, computed_at) VALUES (1, 'w', ?, 't')",
              [json.dumps(t)])  # fmt: skip
    c.execute("INSERT INTO factors (stock_id, as_of_date, factor, raw_json, peer_group, peer_size, calc_version,"
              " created_at) VALUES (1, '2026-10-07', 'momentum', ?, 'g', 10, 10, 't')",
              [json.dumps({"momentum_3m": t["state"]["r3"], "high_52w_proximity": t["state"]["prox"]})])  # fmt: skip
    warnings: list[str] = []
    mem.batch(job.build_market(mem, "KR", date(2026, 10, 8), warnings))  # type: ignore[arg-type]
    assert not [w for w in warnings if "시장 국면" in w]
    a = json.loads(c.execute("SELECT detail_json FROM stock_verdicts").fetchone()[0])["outlook"]["analog"]
    assert a["regime"]["state"] == "bear"
    assert math.isclose(a["regime"]["horizons"][0]["median"], t["by_regime"]["bear"][a["key"]]["h"]["1"]["median"])
