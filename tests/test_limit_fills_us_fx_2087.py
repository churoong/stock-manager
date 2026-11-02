"""미국 분할 지정가 체결 판 운영 일치의 **고정 환율 1,300 판** (25.2087, 25.2084 교차검증 [권고] — handoff "지정가 1,300 판").

25.2084 는 환율 1.0 틀(2059)에서만 돌아, 운영 회차 수량의 ÷ 환율(`buy_orders` 총액)·체결 현금의 × 환율(`make_fill`)과
체결 환율 고르기(`fill_orders` 의 `_fx(날)`)가 빠지거나 뒤집혀도 지났다. 국내 25.1914 L4 틀을 25.2073 의 고정 환율 모듈
(운영 DB 환율 1,300, 입금 = 명목 × 1,300 원) 위에 싣는다 — 운영 체결 판정이 그날 환율 행으로 스스로 채운다. 1914 의 현금
비중 대조는 원화 현금 + 달러 평가 × 환율로 잰다(25.2087 — 예전 꼴은 환율 ≠ 1 에 실을 수 없었다). 지주사·위원회·가치 A 운영
입력은 25.2079 가 고정 환율 모듈에 실은 1921·1934 판. 허용·하한은 25.2084 의 미국 값 그대로(같은 자료·같은 수량)
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from tests import test_limit_fills_us_2084 as lf_us
from tests import test_replay_parity_hdeac_us_fx_2079 as fx_회사  # noqa: F401 — 고정 환율 모듈에 회사 운영입력 등록

_원본 = Path(__file__).with_name("test_limit_fills_1914.py")


def _싣기() -> types.ModuleType:
    src = _원본.read_text(encoding="utf-8")
    assert src.count(lf_us._틀) == 1 and src.count(lf_us._등록) == 1, "국내 지정가 틀의 가져오기 줄이 바뀌었다"
    src = src.replace(lf_us._틀, f"t10 = __import__('sys').modules[{fx_회사.t.__name__!r}]\n").replace(lf_us._등록, "")
    m = types.ModuleType("tests._limit_fills_us_fx_2087")
    m.__file__ = str(_원본)
    sys.modules[m.__name__] = m
    exec(compile(src, str(_원본), "exec"), m.__dict__)
    m.ALLOW_SHARES = lf_us.ALLOW_SHARES_US
    m.L4_하한 = lf_us.L4_하한_US
    return m


lf = _싣기()


def test_고정_환율_틀_위에_실렸다() -> None:
    assert lf.t10 is fx_회사.t and lf.t10.시장 == "US" and lf.t10.환율 == 1300.0


@pytest.mark.parametrize("판", ["zero", "fees"])
@pytest.mark.parametrize("variant", sorted(lf_us.L4_하한_US))
def test_고정_환율에서도_운영_체결_판정과_찬_회차·보유·원가가_같다(variant: str, 판: str) -> None:
    lf.test_운영_체결_판정과_찬_회차·보유·원가가_같다(variant, 판)
