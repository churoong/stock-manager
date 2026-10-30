"""깔때기 ⑧ — 미국 가치 A·모멘텀 C(사기 전 점검 계좌) 리플레이와 운영 미국 가상모의투자의 일치 시험 (25.2065).

국내 틀(`tests/test_replay_parity_ac_1934.py`)을 미국 틀 위에 한 벌 더 싣는다 — 그 파일이 가져다 쓰는 국내 일치 틀(1910)·
H·D·E 틀(1921)을 미국 판(25.2059·25.2064 가 실은 모듈)으로 바꿔 끼운다. 국내 틀의 날짜·종목을 박은 단언(재무악화 날)은 국내
자료의 것이라 옮기지 않고, 같은 단(L0~L3·회의 단)을 같은 허용으로 견준다.

무작위 대조 R1·R2 의 미국 판(`test_replay_parity_r_1929.py` 를 같은 꼴로 싣기)은 결정적으로 갈린다 — 운영 `held_substitutes`
가 옛 보유 자리를 잇는 25.1929 의 알려진 차이가 미국 자료에선 옛 자리 신호가 나와 실제 뽑기를 바꾼다(R1 2024-08-15 종목 14
↔ 9, 25.2067. 25.2065 의 "실행마다 달랐다" 는 틀린 기록). 고치는 것은 R 판 올림 때라 옮기지 않는다.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from tests import test_replay_parity_hde_us_2064 as hde_us
from tests import test_replay_parity_us_2059 as us_base

_가져오기 = {
    "from tests import test_replay_parity_1910 as t\n": us_base.us.__name__,
    "from tests import test_replay_parity_hde_1921 as hde\n": hde_us.hde.__name__,
}


def _싣기(파일: str, 이름: str) -> types.ModuleType:
    원본 = Path(__file__).with_name(파일)
    src = 원본.read_text(encoding="utf-8")
    for 줄, 모듈 in _가져오기.items():
        if 줄 in src:
            assert src.count(줄) == 1
            src = src.replace(줄, f"{줄.split(' as ')[1].strip()} = __import__('sys').modules[{모듈!r}]\n")
    m = types.ModuleType(이름)
    m.__file__ = str(원본)
    sys.modules[m.__name__] = m
    exec(compile(src, str(원본), "exec"), m.__dict__)
    assert m.t is us_base.us, 파일
    return m


ac_us = _싣기("test_replay_parity_ac_1934.py", "tests._parity_ac_us_2065")
t = us_base.us


@pytest.mark.parametrize("variant", ["core_a", "core_c"])
def test_미국_창이_사고_판다(variant: str) -> None:
    rp = t._리플레이(variant, "zero")
    assert sum(len(o.buys) for o in rp.주문.values()) >= 5, variant


@pytest.mark.parametrize("판", ["zero", "fees"])
@pytest.mark.parametrize("variant", ["core_a", "core_c"])
def test_미국_사기_전_점검_계좌가_같다(variant: str, 판: str) -> None:
    for 단 in (t._L0, t._L1, t._L2, t._L3, hde_us.hde._회의_대조):
        틀림 = 단(variant, 판)
        assert not 틀림, (단.__name__, 틀림[:5])
    assert t._관문(variant, 판) <= (1e-4 if 판 == "zero" else t.ALLOW_GATE_RETURN_PCTP)
