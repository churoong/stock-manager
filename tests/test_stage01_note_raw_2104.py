"""0→1 점검 글의 걸린 칸에 판정에 쓴 날값과 문턱을 붙인다 (25.2104, 11-02 결함 회차 [권고]).

판정(`scoreboard.stage01`)은 반올림하지 않은 값으로 하고, 판정판 화면 칸(`web/lib/scoreboard.ts` 의 f2·pct·f1·toFixed(3))은
반올림해 보인다. 그래서 문턱 언저리에서는 걸린 줄과 통과한 줄이 같은 숫자로 보였다(11-03 판정판부터 사람이 이 칸을 보고
그림자로 올릴지 정한다 — funnel.md 3장). 화면은 계산하지 않으므로, 배치가 점검 글에 날값을 실어 둘을 가른다.
"""

from __future__ import annotations

from typing import Any

import pytest

from batch.services import scoreboard as scb
from tests.test_stage01_1950 import 기준, 좋은


@pytest.mark.parametrize(
    ("고침", "통과", "걸린글"),
    [
        # 화면 f2 로 "0.90" — 문턱 0.90 을 넘은 듯 보인다
        ({"dsr": 0.8996}, {"dsr": 0.9}, "DSR(낮은 쪽) 0.8996(>= 0.9)"),
        # pct 로 둘 다 "0.0%"
        ({"excess_cagr": -0.00004}, {"excess_cagr": 0.00004}, "초과 CAGR -4e-05(> 0)"),
        # pct 로 둘 다 "-25.0%" — 기준 MDD −0.20, 허용 5%p
        ({"mdd": -0.2504}, {"mdd": -0.2496}, "MDD(기준 대비) -0.2504(>= 기준 MDD -0.2 − 0.05)"),
        # f1 로 둘 다 "50.0"
        ({"control_lower": 50.0}, {"control_lower": 50.04}, "대조 하한 50(> 50)"),
        # toFixed(3) 로 "0.100"
        ({"bh_q": 0.1004}, {"bh_q": 0.1}, "코호트 BH q 0.1004(<= 0.1)"),
    ],
)
def test_문턱_언저리_걸린_칸은_날값으로_갈린다(고침: dict[str, Any], 통과: dict[str, Any], 걸린글: str) -> None:
    걸림 = scb.stage01({**좋은, **고침}, 기준)
    assert 걸림 is not None and 걸림["verdict"] == "탈락"
    assert scb.stage01_note(걸림) == f"걸림: {걸린글} · 재료 없음: 수용량"
    지남 = scb.stage01({**좋은, **통과}, 기준)
    assert 지남 is not None and 지남["failed"] == [] and scb.stage01_note(지남) == "재료 없음: 수용량"


def test_점검표가_없는_옛_꼴이면_이름만() -> None:
    assert scb.stage01_note({"failed": ["DSR(낮은 쪽)"], "unknown": []}) == "걸림: DSR(낮은 쪽)"


def test_기준_MDD_가_없으면_문턱_글에_숫자_없이() -> None:
    st = scb.stage01({**좋은, "mdd": -0.9}, {"strategy": "core_b"})
    assert st is not None
    칸 = next(c for c in st["checks"] if c["key"] == "mdd_vs_base")
    assert 칸["target"] == ">= 기준 MDD − 0.05" and 칸["ok"] is None
