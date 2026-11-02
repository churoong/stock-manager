"""미국 분할 지정가 체결 판의 운영 일치 시험 (25.2084, 깔때기 ⑧ — 25.2067 `WARN_US_LIMIT_UNVERIFIED` 를 걷는 근거).

국내 25.1914 L4 틀(`tests/test_limit_fills_1914.py` — 운영 `run_step` 이 낸 회차를 운영 체결 판정이 스스로 채우고, 회차·찬
회차·보유·원가·현금을 리플레이 지정가 판과 날마다 견준다)을 **미국 틀 위에** 한 벌 더 싣는다. 그 파일이 가져다 쓰는 국내
일치 틀(1910)을 미국 판(25.2059 `test_replay_parity_us_2059` 가 시장 "US" 로 실은 모듈)으로, 지주사·위원회·가치 A 운영입력
등록(1921·1934)을 미국 판(2064·2065)으로 바꿔 끼운다. 운영 `run_step` 의 시장은 틀의 `시장` 줄(25.2084 — 예전 "KR" 고정).

미국 이름 붙은 차이(허용):
- 보유 주식 수 — 정수 주 내림(허용 ③ 뿌리). 운영은 회차마다 자기 원화 자본으로 수량을 내림하고 엔진은 실수라, 찬 회차마다
  ±1주 안에서 부호가 바뀌며 쌓인다. 미국 합성 자료는 한 종목에 회차가 수십 번 차(종목 18 — G 1~6월 거의 날마다) 국내(최대
  1.42주)보다 크다. 2026-11-02 실측 최대: B 1.43(비용)·G 1.81(0)·2.36(비용, 2024-06-12 종목 18, 943만 주 — 상대 2.5e-7)·
  H 0.79·D 0.75·E 2e-10·A 0.75. 허용 `ALLOW_SHARES_US` = 3.0(실측 약 1.3배). 낸 회차 수량도 같은 허용
- 그 밖(낸 회차·찬 회차·현금 비중·주당 원가)은 국내와 같은 허용(`ALLOW_REL`, 비용 판 더 산 로트는 ⑦)
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from batch.jobs import core_backtest as cj
from batch.services import core_strategy as cs
from tests import test_replay_parity_ac_us_2065  # noqa: F401 — 미국 틀에 지주사·위원회·가치 A 운영입력 등록
from tests import test_replay_parity_us_2059 as us_base

_원본 = Path(__file__).with_name("test_limit_fills_1914.py")
_틀 = "from tests import test_replay_parity_1910 as t10\n"
_등록 = ("from tests import (\n    test_replay_parity_ac_1934,  # noqa: F401 — 가치 A 운영입력 등록(25.1948)\n"
         "    test_replay_parity_hde_1921,  # noqa: F401 — 지주사·위원회 운영입력 등록(25.1945)\n)\n")  # fmt: skip
ALLOW_SHARES_US = 3.0
#: 헛돌지 않게 — (낸 회차, 찬 회차, 일부만 찬 종목-날, 하나도 안 찬 종목-날, 찬 더 사기, 견준 보유) 하한. 2026-11-02 실측
#: B (816, 157, 102, 163, 94, 552)·G (723, 139, 95, 141, 89, 630)·H (123, 11, 6, 34, 3, 171)·D (42, 5, 4, 10, 3, 146)·
#: E (69, 3, 0, 22, 0, 7)·A (42, 5, 4, 10, 3, 91) 의 약 8할(비용 판도 같은 수). E 는 창 안에 고른 종목이 적다
L4_하한_US = {"core_b": (650, 125, 80, 130, 75, 440), "core_g": (580, 110, 75, 110, 70, 500),
            "core_h": (98, 8, 4, 27, 2, 136), "core_d": (33, 4, 3, 8, 2, 116), "core_e": (55, 2, 0, 17, 0, 5),
            "core_a": (33, 4, 3, 8, 2, 72)}  # fmt: skip


def _싣기() -> types.ModuleType:
    src = _원본.read_text(encoding="utf-8")
    assert src.count(_틀) == 1 and src.count(_등록) == 1, "국내 지정가 틀의 가져오기 줄이 바뀌었다"
    src = src.replace(_틀, f"t10 = __import__('sys').modules[{us_base.us.__name__!r}]\n").replace(_등록, "")
    m = types.ModuleType("tests._limit_fills_us_2084")
    m.__file__ = str(_원본)
    sys.modules[m.__name__] = m
    exec(compile(src, str(_원본), "exec"), m.__dict__)
    m.ALLOW_SHARES = ALLOW_SHARES_US
    m.L4_하한 = L4_하한_US
    return m


lf = _싣기()


def test_미국_틀_위에_실렸다() -> None:
    assert lf.t10 is us_base.us and lf.t10.시장 == "US"
    # 일치로 묶인 회차 변형 = 실행기의 목록(넓히려면 여기 시험을 먼저 더한다), = 지정가가 켜지는 회차 진입 변형 전부
    assert set(L4_하한_US) == set(lf.L4_변형들) == cj.US_LIMIT_PARITY_VERIFIED
    회차 = {k for k in set(cs.VARIANTS) | set(cs.EXPLORE_VARIANTS) if cs.variant_spec(k).entry == "tranche"}
    assert 회차 == cj.US_LIMIT_PARITY_VERIFIED


@pytest.mark.parametrize("판", ["zero", "fees"])
@pytest.mark.parametrize("variant", sorted(L4_하한_US))
def test_미국_운영_체결_판정과_찬_회차·보유·원가가_같다(variant: str, 판: str) -> None:
    lf.test_운영_체결_판정과_찬_회차·보유·원가가_같다(variant, 판)
