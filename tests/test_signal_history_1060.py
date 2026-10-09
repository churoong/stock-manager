"""이 종목 신호 성적 (docs/analysis.md 37장, docs/infra.md 25.1060).

신호는 조건이 이어지는 동안 날마다 다시 적힌다 — 그대로 세면 한 번이 수십 번으로 부풀어 "20번 중 18번 올랐다" 같은
가짜 확신이 된다. 그래서 끊김 문턱으로 한 번씩 묶는지, 기간이 다르면 따로 세는지, 일일 의견에 실리는지를 묶는다.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

from batch.core import db
from batch.jobs import verdicts as job
from batch.services import insights as ins
from tests.test_forecast_track_1037 import _시장
from tests.test_portfolio_job import MemClient


def _행(d: str, h: str = "short", r20: float | None = 0.05, b20: float | None = 0.01, **kw) -> dict:
    return {"as_of_date": d, "horizon": h, "ret_5d": None, "ret_20d": r20, "ret_60d": None,
            "hit_target": kw.get("t", 0), "hit_stop": kw.get("s", 0), "bench_ret_20d": b20}  # fmt: skip


def test_이어지는_신호는_한_번으로() -> None:
    d0 = date(2026, 1, 5)
    행 = [_행((d0 + timedelta(days=i)).isoformat(), r20=0.10, t=1) for i in range(10)]  # 열흘 이어진 한 번
    행 += [_행((d0 + timedelta(days=10 + ins.SIGNAL_GAP_DAYS + 1)).isoformat(), r20=-0.04, b20=0.0, s=1)]  # 끊긴 뒤 새 신호
    행 += [_행(d0.isoformat(), h="long", r20=None)]  # 기간이 다르면 따로
    s = ins.signal_history(행)
    assert s["signals"] == 3 and s["rows"] == 12 and s["n20"] == 2
    assert math.isclose(s["avg20"], (0.10 - 0.04) / 2) and s["up20"] == 1
    assert math.isclose(s["excess20"], ((0.10 - 0.01) + (-0.04 - 0.0)) / 2)
    assert (s["targets"], s["stops"]) == (1, 1) and s["recent"][0]["ret_20d"] == -0.04
    # 끊김 문턱 안이면 같은 신호
    가까이 = [_행("2026-01-05"), _행((date(2026, 1, 5) + timedelta(days=ins.SIGNAL_GAP_DAYS)).isoformat())]
    assert ins.signal_history(가까이)["signals"] == 1
    assert ins.signal_history([]) is None


def test_일일_의견에_실린다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    _시장(c, 기준가=100.0, 지금가=104.0)
    for d, r in (("2026-03-02", 0.08), ("2026-03-03", 0.07), ("2026-06-01", -0.02)):
        c.execute("INSERT INTO signal_outcomes (stock_id, as_of_date, horizon, ret_20d, bench_ret_20d, hit_target,"
                  " hit_stop, days_available, computed_at) VALUES (1, ?, 'short', ?, 0.0, 0, 0, 60, 't')", [d, r])  # fmt: skip
    mem.batch(job.build_market(mem, "KR", date(2026, 10, 8), []))  # type: ignore[arg-type]
    d = json.loads(c.execute("SELECT detail_json FROM stock_verdicts").fetchone()[0])
    assert d["signal_history"]["signals"] == 2 and math.isclose(d["signal_history"]["avg20"], 0.03)
    assert any(r.startswith("이 종목에 난 신호(지난 1년 2번)") for r in d["reasons"])
