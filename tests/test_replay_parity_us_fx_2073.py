"""미국 리플레이↔운영 일치 시험의 **고정 환율 판** (25.2073, 25.2063 [권고] — handoff "고정 환율 판").

25.2059 의 미국 판은 운영 DB 환율을 1.0 으로만 둬, 운영 `buy_orders` 의 자본 ÷ 환율(달러 사이징)·체결의 × 환율(원화 현금)·
평가의 × 환율 방향이 뒤집히거나 두 번 들어가도 지났다. 국내 틀(`tests/test_replay_parity_1910.py`)을 시장 "US"·환율 1,300 으로
한 벌 더 싣는다 — 운영 입금은 명목 자본 × 1,300 원(달러로 같은 명목), 베낀 체결은 그 환율로. 리플레이는 시장 통화 장부(달러)
그대로라, 운영이 환율을 바르게 쓰면 L0~L3 이 1.0 판과 같은 허용 안에서 같다. 환율은 날마다 같은 값이라 환율 움직임은
여전히 이 시험 밖이다(이름 붙은 차이 — 국내·미국 한 원화 현금).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

_원본 = Path(__file__).with_name("test_replay_parity_1910.py")
환율 = 1300.0


def _싣기() -> types.ModuleType:
    src = _원본.read_text(encoding="utf-8")
    for 줄 in ('\n시장 = "KR"\n', "\n환율 = 1.0\n"):
        assert src.count(줄) == 1, f"국내 틀의 {줄.strip()} 줄이 바뀌었다"
    src = src.replace('\n시장 = "KR"\n', '\n시장 = "US"\n').replace("\n환율 = 1.0\n", f"\n환율 = {환율}\n")
    m = types.ModuleType("tests._parity_us_fx_2073")
    m.__file__ = str(_원본)
    sys.modules[m.__name__] = m
    exec(compile(src, str(_원본), "exec"), m.__dict__)
    return m


fx = _싣기()


def test_고정_환율_판이_실렸다() -> None:
    assert fx.시장 == "US" and fx.환율 == 환율
    c = fx._db("B", fx.비용판["zero"][0])
    assert c.q("SELECT DISTINCT rate FROM fx_rates") == [(환율,)]
    assert c.q("SELECT amount_krw FROM paper_cash_flows") == [(int(fx.NOTIONAL * 환율),)]


@pytest.mark.parametrize("판", ["zero", "fees"])
@pytest.mark.parametrize("variant", ["core_b", "core_g"])
def test_고정_환율에서도_장부·매도·결정·매수_비중이_같다(variant: str, 판: str) -> None:
    for 단 in (fx._L0, fx._L1, fx._L2, fx._L3):
        틀림 = 단(variant, 판)
        assert not 틀림, (단.__name__, 틀림[:5])
    assert fx._관문(variant, 판) <= (1e-4 if 판 == "zero" else fx.ALLOW_GATE_RETURN_PCTP)
    # 운영이 실제로 샀다 — 환율 거름(사이징이 0)이면 L 단이 헛돈다
    assert sum(len(h.buys) for h in fx._운영(variant, 판).values()) >= 10
