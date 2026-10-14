"""짧은 사업연도 가드를 25.1286·25.1293 이 빠뜨린 연간 비교에 (docs/infra.md 25.1326, 20회차 P2).

- 공시 반응 YoY(40장)·상승의 출처(41장): 어느 한쪽이 짧은 해면 모름 — 연환산하지 않는다. 분·반기 행은 건드리지 않는다
- 매도 플래그 ① 적자 전환: 지우지 않고 기간 주석만(부호 판정이라 짧은 해의 손실도 진짜 손실 — 검증 A·B)
- 적립 G8: 창 안에 짧은 해가 있을 때만 참고 행, 판정은 그대로
"""

from __future__ import annotations

import math
from datetime import date

from batch.services import event_history as ev
from batch.services import sell_flags as sf
from tests.test_accumulation import YEARS, good, judge
from tests.test_event_history_1063 import _날, _재무
from tests.test_sell_flags import TODAY, codes, holding


def _반응(재무: list[dict]) -> dict:
    날 = _날(600)
    c = [100.0] * 600
    i = 날.index("2025-03-15")
    c[i:] = [110.0] * (600 - i)
    return ev.earnings_reactions(재무, 날, c, {d: 1000.0 for d in 날})


def test_공시_반응_YoY_는_짧은_해와_견주지_않는다() -> None:
    짧은 = _재무(2024, "11011", "2025-03-15", 150) | {"period_days": 92}
    e = _반응([_재무(2023, "11011", "2024-03-15", 100), 짧은])
    assert e["recent"][0]["yoy"] is None and e["grew"] is None  # 예전: yoy 0.5 로 "늘었다" 에 셈
    전년짧음 = _재무(2023, "11011", "2024-03-15", 100) | {"period_days": 200}
    assert _반응([전년짧음, _재무(2024, "11011", "2025-03-15", 150)])["recent"][0]["yoy"] is None


def test_분기_행은_기간이_짧아도_그대로() -> None:
    e = _반응([_재무(2023, "11013", "2024-03-15", 100) | {"period_days": 90},
               _재무(2024, "11013", "2025-03-15", 150) | {"period_days": 90}])  # fmt: skip
    assert e["recent"][0]["yoy"] == 0.5


def _출처(시작_일수: int | None, 끝_일수: int | None) -> dict | None:
    날 = _날(1300, date(2021, 1, 1))
    c = [100.0] * 1299 + [150.0]
    재무 = [_재무(2021, "11011", "2022-03-20", 0, ni=100) | {"period_days": 시작_일수},
          _재무(2024, "11011", "2025-03-20", 0, ni=120) | {"period_days": 끝_일수}]  # fmt: skip
    return ev.return_sources(재무, [], 날, c, 1000.0)


def test_상승의_출처는_어느_끝이든_짧은_해면_모름() -> None:
    assert _출처(None, 92) is None and _출처(150, 365) is None
    s = _출처(365, None)
    assert s is not None and math.isclose(s["earnings"], 0.2)


def test_적자_전환은_지우지_않고_기간만_적는다() -> None:
    h = holding(op_income_latest=-5e8, op_income_prev=10e8, fiscal_year_latest=2025, op_income_currency="KRW",
                op_income_prev_currency="KRW", op_income_period_days=(92, 365))  # fmt: skip
    f = sf.evaluate(h, TODAY)
    assert codes(f) == [("yellow", "재무악화")]
    assert "올해 사업연도 92일(12개월 아님)" in f[0].criteria[0]["display"]
    보통 = sf.evaluate(holding(op_income_latest=-5e8, op_income_prev=10e8, fiscal_year_latest=2025,
                              op_income_currency="KRW", op_income_prev_currency="KRW"), TODAY)  # fmt: skip
    assert "12개월 아님" not in 보통[0].criteria[0]["display"]


def test_G8_은_짧은_해가_있을_때만_참고_행() -> None:
    inp = good()
    inp.years[YEARS[-1]].period_days = 92
    j = judge(inp)
    행 = [r for r in j.criteria if r["label"] == "짧은 사업연도 포함"]
    assert len(행) == 1 and 행[0]["passed"] is None and f"FY{YEARS[-1]} 92일" in 행[0]["display"]
    assert j.passed == judge(good()).passed  # 판정은 그대로
    assert not [r for r in judge(good()).criteria if r["label"] == "짧은 사업연도 포함"]
