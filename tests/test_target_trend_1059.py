"""목표가 흐름 (docs/analysis.md 36장, docs/infra.md 25.1059).

"증권사들이 올리고 있나 내리고 있나" 가 틀리면 반대로 읽힌다. 같은 증권사의 바로 앞 목표가와만 견주는지,
허용 폭 안의 변경을 세지 않는지, 중앙값의 길이 그날까지의 마지막 목표가로 찍히는지를 묶는다.
"""

from __future__ import annotations

import math
from datetime import date

from batch.services import insights as ins
from batch.services import verdict as vd

오늘 = date(2026, 10, 9)


def _o(d: str, b: str, x: float) -> dict:
    return {"date": d, "broker": b, "target_price": x}


def test_같은_증권사의_바로_앞과만_견준다() -> None:
    의견 = [_o("2026-08-01", "가", 100), _o("2026-09-01", "가", 120), _o("2026-09-15", "나", 90),
          _o("2026-10-01", "나", 81), _o("2026-10-02", "다", 200), _o("2026-10-03", "가", 120.3)]  # fmt: skip
    t = ins.target_trend(의견, 오늘, 90)
    # 가 100→120 올림, 나 90→81 내림, 가 120→120.3 은 허용 폭(0.5%) 안, 다는 처음이라 세지 않는다
    assert (t["raises"], t["cuts"]) == (1, 1) and math.isclose(t["avg_change"], (0.2 - 0.1) / 2)
    # 60일 전(08-10): 가 100 하나 → 30일 전(09-09): 가 120 → 지금: 가 120.3·나 81·다 200 → 중앙값 120.3
    assert [(p["days_ago"], p["brokers"], p["median"]) for p in t["points"]] == [(60, 1, 100), (30, 1, 120), (0, 3, 120.3)]
    # 창 밖 의견은 보지 않는다
    assert ins.target_trend([_o("2026-06-01", "가", 100)], 오늘, 90) is None


def test_진단_줄() -> None:
    의견 = [_o("2026-08-01", "가", 100), _o("2026-09-20", "가", 110)]
    o = vd.outlook(close=100.0, close_date="d", currency="KRW", momentum=None, risk=None, band=None, opinions=의견,
                   today=오늘)  # fmt: skip
    줄 = next(x for x in o["lines"] if x.startswith("목표가 흐름("))
    assert "올림 1·내림 0" in 줄 and "평균 변경 +10.0%" in 줄 and "지금" in 줄
    assert o["target_trend"]["points"][-1]["median"] == 110
