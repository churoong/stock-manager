"""깔때기 ⑧ — 미국 지주사 H·위원회 D·E 리플레이와 운영 미국 가상모의투자의 일치 시험 (25.2064, docs/funnel.md 4장 7).

국내 H·D·E 틀(`tests/test_replay_parity_hde_1921.py`)을 **미국 틀 위에** 한 벌 더 싣는다 — 그 파일이 가져다 쓰는 국내 일치
틀(`test_replay_parity_1910`)을 미국 판(`tests/test_replay_parity_us_2059.py` 가 시장 "US" 로 실은 모듈)으로 바꿔 끼운다.
운영 DB 의 지수는 SP500(국면·결정 원장 성과), 환율 1.0 행, 헌장 그대로. 견주는 단: L0~L3 + 회의 단(지주사 회사 가중치·
표본·추천·고름, 위원회 안건·통과·가결 수) — 비용 0 은 허용 없이, 비용 판은 국내 틀과 같은 허용 안에서.
미국 조사 자료는 증권사·수급·공시부가 "미국 종목 … 모으지 않는다" 로 기권한다(운영·리플레이 같음, 25.2056).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from tests import test_replay_parity_us_2059 as us_base

_원본 = Path(__file__).with_name("test_replay_parity_hde_1921.py")
_국내_줄 = "from tests import test_replay_parity_1910 as t\n"


def _싣기() -> types.ModuleType:
    src = _원본.read_text(encoding="utf-8")
    assert src.count(_국내_줄) == 1, "국내 H·D·E 틀의 가져오기 줄이 바뀌었다"
    m = types.ModuleType("tests._parity_hde_us_2064")
    m.__file__ = str(_원본)
    sys.modules[m.__name__] = m
    src = src.replace(_국내_줄, f"t = __import__('sys').modules[{us_base.us.__name__!r}]\n")
    exec(compile(src, str(_원본), "exec"), m.__dict__)
    return m


hde = _싣기()


def test_미국_틀_위에_실렸다() -> None:
    assert hde.t is us_base.us and hde.t.시장 == "US"
    # 지주사·위원회 운영 입력이 미국 틀에 걸렸다(국내 틀은 건드리지 않는다)
    assert {"weighted", "committee"} <= set(us_base.us.운영입력들)


@pytest.mark.parametrize("variant", ["core_h", "core_d", "core_e"])
def test_미국_창이_사고_판다(variant: str) -> None:
    rp = hde.t._리플레이(variant, "zero")
    assert sum(len(o.buys) for o in rp.주문.values()) >= 3, variant


@pytest.mark.parametrize("variant", ["core_h", "core_d", "core_e"])
def test_미국_비용_0_이면_장부·매도·결정·매수_비중이_허용_없이_같다(variant: str) -> None:
    hde.test_비용_0_이면_장부·매도·결정·매수_비중이_허용_없이_같다(variant)


@pytest.mark.parametrize("variant", ["core_h", "core_d", "core_e"])
def test_미국_비용_판도_이름_붙은_허용_안에서_같다(variant: str) -> None:
    hde.test_비용_판도_이름_붙은_허용_안에서_같다(variant)
