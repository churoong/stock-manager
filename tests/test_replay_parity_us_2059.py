"""깔때기 ⑧-3b — 미국 리플레이(`CoreStrategy` 시장 통화 장부)와 운영 미국 가상모의투자(`jobs/paper.run_step(market="US")`)의
일치 시험 (25.2059, docs/funnel.md 4장 7).

국내 일치 시험 틀(`tests/test_replay_parity_1910.py`)을 **시장만 "US" 로 바꿔** 한 벌 더 싣는다 — 같은 합성 자료(미국 거래일,
달러, NASDAQ·NYSE, 국면·벤치마크 SP500), 운영 DB 에는 날마다 환율 1.0 행(`fx_rates`)을 넣어 운영 원화 장부와 리플레이 달러
장부가 같은 단위다. 견주는 단·허용 차이는 국내 틀과 같다(L0 판단 직전 장부, L1 매도 종목·사유, L2 결정 원장, L3 매수 비중).
이름 붙은 차이: 운영 계좌의 국내·미국 한 원화 현금과 날마다의 환율 움직임(환율 1.0 고정이라 이 시험에는 닿지 않는다).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

_원본 = Path(__file__).with_name("test_replay_parity_1910.py")


def _싣기() -> types.ModuleType:
    src = _원본.read_text(encoding="utf-8")
    assert src.count('\n시장 = "KR"\n') == 1, "국내 틀의 시장 줄이 바뀌었다"
    m = types.ModuleType("tests._parity_us_2059")
    m.__file__ = str(_원본)
    sys.modules[m.__name__] = m  # dataclass 가 모듈을 찾는다
    exec(compile(src.replace('\n시장 = "KR"\n', '\n시장 = "US"\n'), str(_원본), "exec"), m.__dict__)
    return m


us = _싣기()


def test_미국_합성_창이_견줄_거리를_지난다() -> None:
    assert us.시장 == "US" and us.통화 == "USD"
    us.test_합성_창이_견줄_거리를_지난다()


@pytest.mark.parametrize("variant", ["core_b", "core_g"])
def test_미국_비용_0_이면_장부·매도·결정·매수_비중이_허용_없이_같다(variant: str) -> None:
    us.test_비용_0_이면_장부·매도·결정·매수_비중이_허용_없이_같다(variant)


@pytest.mark.parametrize("variant", ["core_b", "core_g"])
def test_미국_매도_종목과_사유가_같다(variant: str) -> None:
    us.test_매도_종목과_사유가_같다(variant)


@pytest.mark.parametrize("variant", ["core_b", "core_g"])
def test_미국_결정_원장이_같다(variant: str) -> None:
    us.test_결정_원장이_같다(variant)


@pytest.mark.parametrize("variant", ["core_b", "core_g"])
def test_미국_매수_비중이_같다(variant: str) -> None:
    us.test_매수_비중이_같다(variant)
