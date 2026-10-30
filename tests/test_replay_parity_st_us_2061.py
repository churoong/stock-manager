"""깔때기 ⑧-4 — S·T 리플레이(`swing_day_replay.simulate_account`, 시장 통화 장부)와 운영 미국 가상모의투자
(`jobs/paper.run_step(market="US")`)의 일치 시험 (25.2061, docs/funnel.md 4장 7).

국내 S·T 일치 시험 틀(`tests/test_replay_parity_st_2037.py`)을 **시장만 "US" 로 바꿔** 한 벌 더 싣는다 — 같은 합성 자료(미국
거래일, 달러, NASDAQ), 운영 DB 에 날마다 환율 1.0 행. 견주는 것: 체결 하나하나·날마다 현금(S·T × 보통·빠듯), 관문 막힘,
관문 문맥, 중간 입금 날. 미국엔 가격제한이 없어 국내 틀의 상한가 시가·하한가 잠김 길은 여기서 **양쪽 모두** 지나지 않는다
(국내 틀의 `test_자료가_견줄_거리를_지난다` 는 국내 길을 묶는 시험이라 옮기지 않는다 — 대신 미국에선 그 날에 사고 판다는 것을
단언한다).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from batch.jobs import paper as job
from batch.services import report_picks

_원본 = Path(__file__).with_name("test_replay_parity_st_2037.py")


def _싣기() -> types.ModuleType:
    src = _원본.read_text(encoding="utf-8")
    assert src.count('\n시장 = "KR"\n') == 1, "국내 틀의 시장 줄이 바뀌었다"
    m = types.ModuleType("tests._parity_st_us_2061")
    m.__file__ = str(_원본)
    sys.modules[m.__name__] = m
    exec(compile(src.replace('\n시장 = "KR"\n', '\n시장 = "US"\n'), str(_원본), "exec"), m.__dict__)
    return m


us = _싣기()


@pytest.fixture(autouse=True)
def _고정(monkeypatch: pytest.MonkeyPatch) -> None:
    """국내 틀의 autouse 고정과 같다 — 실린 모듈의 고정은 pytest 가 모른다"""
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())


@pytest.mark.parametrize("판", ["보통", "빠듯"])
@pytest.mark.parametrize("account", ["S", "T"])
def test_미국_체결_하나하나가_운영과_같다(account: str, 판: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert us.시장 == "US" and us.통화 == "USD"
    us.test_체결_하나하나가_운영과_같다(account, 판, monkeypatch)


def test_미국엔_가격제한이_없어_국내가_막는_날에_사고_판다() -> None:
    """국내 틀의 상한가 시가 날(종목 3, 날들[2] 시가 +29.7%)에 T 가 미국에선 산다 — 주문 통화가 시장에서 와서 국내 상한가 시가
    막기가 걸리지 않는다(예전처럼 "KRW" 로 박으면 막힌다)"""
    t = us._리플레이("T")
    assert not any(k.startswith("상한가 시가") for g in t.log for k in g["expired"])
    assert any(f.stock_id == 3 and f.fill_date == us.날들[2] and f.side == "buy" for f in t.fills)
    assert {f.currency for f in t.fills} == {"USD"}


def test_미국_관문_막힘이_운영과_같다(monkeypatch: pytest.MonkeyPatch) -> None:
    us.test_관문_막힘이_운영과_같다(monkeypatch)


@pytest.mark.parametrize("판", ["보통", "빠듯"])
@pytest.mark.parametrize("account", ["S", "T"])
def test_미국_관문_문맥이_날마다_운영과_같다(account: str, 판: str, monkeypatch: pytest.MonkeyPatch) -> None:
    us.test_관문_문맥이_날마다_운영과_같다(account, 판, monkeypatch)


def test_미국_중간_입금_손실_날도_관문은_입금을_뺀_수익으로_막는다(monkeypatch: pytest.MonkeyPatch) -> None:
    us.test_중간_입금_손실_날도_관문은_입금을_뺀_수익으로_막는다(monkeypatch)
