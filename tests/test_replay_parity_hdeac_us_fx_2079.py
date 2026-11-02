"""미국 지주사 H·위원회 D·E·가치 A·모멘텀 C 일치 시험의 **고정 환율 1,300 판** (25.2079, 25.2073 교차검증 note).

25.2073 은 B·G 만 환율 1,300 으로 견줬다. H·D·E(25.2064)·A·C(25.2065) 미국 판은 운영 DB 환율 1.0 뿐이라, 지주사·위원회·
사기 전 점검 경로(가결 종목을 `preselected` 로 배분, `prebuy_sell_flags`)가 환율을 바른 방향으로 한 번만 쓰는지는 묶이지
않았다. 국내 H·D·E 틀(1921)·A·C 틀(1934)을 25.2073 이 실은 고정 환율 모듈 위에 한 벌 더 싣는다 — 같은 단(L0~L3·회의 단·
관문)을 같은 허용으로 견준다. 환율이 날마다 움직이는 것은 여전히 시험 밖(이름 붙은 차이), 현금 모자람·위험 점검 막음 자리의
환율 방향은 25.2078 손 사례가 묶는다.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from batch.services import core_strategy as cs
from tests import test_replay_parity_us_fx_2073 as fx_base

t = fx_base.fx
_틀 = "from tests import test_replay_parity_1910 as t\n"
_hde_줄 = "from tests import test_replay_parity_hde_1921 as hde\n"


def _싣기(파일: str, 이름: str, 바꿈: dict[str, str]) -> types.ModuleType:
    원본 = Path(__file__).with_name(파일)
    src = 원본.read_text(encoding="utf-8")
    for 줄, 모듈 in 바꿈.items():
        assert src.count(줄) == 1, f"{파일} 의 가져오기 줄이 바뀌었다: {줄.strip()}"
        src = src.replace(줄, f"{줄.split(' as ')[1].strip()} = __import__('sys').modules[{모듈!r}]\n")
    m = types.ModuleType(이름)
    m.__file__ = str(원본)
    sys.modules[m.__name__] = m
    exec(compile(src, str(원본), "exec"), m.__dict__)
    assert m.t is t, 파일
    return m


hde = _싣기("test_replay_parity_hde_1921.py", "tests._parity_hde_us_fx_2079", {_틀: t.__name__})
ac = _싣기("test_replay_parity_ac_1934.py", "tests._parity_ac_us_fx_2079", {_틀: t.__name__, _hde_줄: hde.__name__})


def test_고정_환율_틀_위에_실렸다() -> None:
    assert t.시장 == "US" and t.환율 == fx_base.환율 != 1.0
    assert ac.hde is hde
    assert {"weighted", "committee"} <= set(t.운영입력들)


@pytest.mark.parametrize("판", ["zero", "fees"])
@pytest.mark.parametrize("variant", ["core_h", "core_d", "core_e", "core_a", "core_c"])
def test_고정_환율에서도_회사_계좌가_같다(variant: str, 판: str) -> None:
    for 단 in (t._L0, t._L1, t._L2, t._L3, hde._회의_대조):
        틀림 = 단(variant, 판)
        assert not 틀림, (단.__name__, 틀림[:5])
    assert t._관문(variant, 판) <= (1e-4 if 판 == "zero" else t.ALLOW_GATE_RETURN_PCTP)
    assert not [k for k in t.베낌_0주 if k[0] == cs.variant_spec(variant).account and k[1] == 판]
    # 운영이 실제로 샀다 — 환율 거름(사이징 0)이면 L 단이 헛돈다
    assert sum(len(h.buys) for h in t._운영(variant, 판).values()) >= 3, variant
