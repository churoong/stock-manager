"""성적 가중 합의 (docs/analysis.md 30장, docs/infra.md 25.1056).

네 눈을 "잘 맞혀 온 만큼" 믿는다. 성적이 없는데 가중치를 지어내면 근거 없는 숫자다 — 그래서 **표본이 모자라면
값을 내지 않고 언제부터 내는지만** 말하는지, 오차가 작은 눈이 더 무거운지, 일일 의견에 실리는지를 묶는다.
"""

from __future__ import annotations

import json
import math
from datetime import date

from batch.core import db
from batch.jobs import verdicts as job
from batch.services import forecast_track as ft
from batch.services import insights as ins
from tests.test_forecast_track_1037 import _시장
from tests.test_portfolio_job import MemClient

합의 = {"views": {"capm": 0.08, "consensus": 0.30, "scenario": -0.05}, "up": 2, "n": 3, "spread": 0.35}


def _칸(n: int, 평균오차: float) -> list[float]:
    return [n, 0, 0, 0, n, n // 2, n * 평균오차]


def test_성적이_모자라면_값을_내지_않고_언제부터인지_말한다() -> None:
    시장 = {"capm": {"12": _칸(ft.MIN_SAMPLE - 1, 0.1)}, "consensus": {"1": _칸(500, 0.1)}}
    w = ins.weighted_agreement(합의, 시장, "2026-10-08")
    assert w["value"] is None and w["available_from"] == "2027-10-08"
    assert w["pending"] == ["capm", "consensus", "scenario"] and w["n"] == {"capm": ft.MIN_SAMPLE - 1}
    assert ins.weighted_agreement({"views": {"capm": 0.1}}, 시장, None) is None


def test_오차가_작은_눈이_무겁다() -> None:
    시장 = {"capm": {"12": _칸(100, 0.10)}, "consensus": {"12": _칸(100, 0.30)}}
    w = ins.weighted_agreement(합의, 시장, "2026-10-08")
    # 무게 1/0.1 : 1/0.3 = 3 : 1
    assert math.isclose(w["weights"]["capm"], 0.75) and math.isclose(w["weights"]["consensus"], 0.25)
    assert math.isclose(w["value"], round(0.75 * 0.08 + 0.25 * 0.30, 4)) and w["left_out"] == ["scenario"]


def _두_눈(c) -> None:  # noqa: ANN001
    _시장(c, 기준가=100.0, 지금가=104.0)
    c.execute("INSERT INTO kr_opinions (stock_id, date, broker, target_price, source, fetched_at)"
              " VALUES (1, '2026-09-20', '가', 130, 'kis', 't')")  # fmt: skip


def test_일일_의견에_실린다_성적이_없으면_언제부터() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    _두_눈(mem.conn)
    mem.batch(job.build_market(mem, "KR", date(2026, 10, 8), []))  # type: ignore[arg-type]
    d = json.loads(mem.conn.execute("SELECT detail_json FROM stock_verdicts").fetchone()[0])
    w = d["outlook"]["agreement"]["weighted"]
    assert w["value"] is None and w["available_from"] == "2027-09-07"  # 첫 기록일(9-07) + 1년


def test_일일_의견에_실린다_성적이_있으면_가중() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    _두_눈(mem.conn)
    db.set_setting(mem, job.track_key("KR"), {"since": "2025-09-01", "through": {"1": "2026-09-07"},  # type: ignore[arg-type]
                                              "market": {"capm": {"12": _칸(100, 0.1)},
                                                         "consensus": {"12": _칸(100, 0.3)}}})  # fmt: skip
    mem.batch(job.build_market(mem, "KR", date(2026, 10, 8), []))  # type: ignore[arg-type]
    d = json.loads(mem.conn.execute("SELECT detail_json FROM stock_verdicts").fetchone()[0])
    w = d["outlook"]["agreement"]["weighted"]
    assert math.isclose(w["weights"]["capm"], 0.75)
    assert any(r.startswith("성적 가중 1년 예상:") for r in d["reasons"])
