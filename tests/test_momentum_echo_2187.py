"""에코 모멘텀 운영 교체 — 점수 판 12·근거표 두 가격 행 (docs/infra.md 25.2187, factors.md 3.4·12.12).

11-03 판정 실행에서 사전 등록 A/B(12.12·12.13)를 두 나라 모두 넘었다. 교체 근거표는 25.903 이 "교체가 채택될 때 만든다" 고
남겨 둔 두 가격 행(c−126·c−273) — 배치가 쓴 행을 `raw_json` 예약 키로 남긴다(웹이 `prices` 를 다시 세면 같은 행을 못 찾는다).
"""

from __future__ import annotations

import pytest

from batch.services import scoring as sc


def _계열(n: int = 300) -> tuple[list[str], list[float]]:
    dates = [f"d{i:04d}" for i in range(n)]
    closes = [100.0 + i for i in range(n)]
    return dates, closes


def test_두_가격_행으로_에코_값을_손으로_다시_낸다() -> None:
    dates, closes = _계열()
    note = sc.momentum_points_note(dates, closes)
    assert note is not None and note["as_of"] == dates[-1]
    assert note["p126"] == {"date": dates[-127], "close": closes[-127]}
    assert note["p273"] == {"date": dates[-274], "close": closes[-274]}
    값 = sc.momentum_metrics(closes)["momentum_echo"]
    assert 값 == pytest.approx(note["p126"]["close"] / note["p273"]["close"] - 1)


def test_계열이_모자라면_근거_행도_없다() -> None:
    dates, closes = _계열(274)
    assert sc.momentum_points_note(dates, closes) is not None
    assert sc.momentum_points_note(dates[1:], closes[1:]) is None
    assert sc.momentum_metrics(closes[1:])["momentum_echo"] is None  # 값이 없으면 근거도 없다 — 같은 경계
    assert sc.momentum_points_note(dates[:-1], closes) is None  # 날짜·종가 길이가 다르면 지어내지 않는다


def test_모멘텀_raw_json_에_에코와_두_가격_행이_실린다() -> None:
    dates, closes = _계열()
    inputs = []
    for i in range(40):
        cl = [c * (1 + i / 100) for c in closes]
        inputs.append(sc.StockInput(stock_id=i + 1, market="KOSPI", metrics=sc.momentum_metrics(cl),
                                    momentum_points=sc.momentum_points_note(dates, cl)))  # fmt: skip
    모멘텀 = [r for r in sc.score_factors(inputs) if r.factor == "momentum"]
    r = next(x for x in 모멘텀 if x.stock_id == 1)
    assert "momentum_echo" in r.raw and "momentum_vol_adjusted" not in r.raw
    assert r.raw[sc.MOMENTUM_POINTS_KEY]["p273"]["date"] == dates[-274]
    밸류 = next(x for x in sc.score_factors(inputs) if x.factor == "value" and x.stock_id == 1)
    assert sc.MOMENTUM_POINTS_KEY not in 밸류.raw  # 모멘텀 팩터에만


def test_저장된_원값에서_다시_낼_때_예약_키를_지표로_읽지_않는다() -> None:
    """`inputs_from_stored` 는 `_` 로 시작하는 키를 건너뛴다(25.2187 손댈 곳 조사) — 예약 키가 지표로 섞이지 않는다"""
    assert sc.MOMENTUM_POINTS_KEY.startswith("_")


def test_시작_종가가_0_이하면_근거_행도_없다() -> None:
    """에코가 None 인데 '에코 - = …' 이 찍히지 않게(25.2201 교차검증 [참고])"""
    dates = [f"d{i:04d}" for i in range(300)]
    closes = [100.0] * 300
    closes[-274] = 0.0
    assert sc.momentum_points_note(dates, closes) is None


def test_저장값에서_다시_낼_때_예약_키는_지표로_들지_않는다() -> None:
    """25.2201 교차검증 [참고] — 시험 이름이 약속하는 규칙(`inputs_from_stored` 가 `_` 키를 건너뜀)을 실제로 본다"""
    rows = [{"stock_id": 1, "market": "KOSPI", "sector": None, "factor": "momentum",
             "raw": {"momentum_echo": 0.1, sc.MOMENTUM_POINTS_KEY: {"p126": {"date": "d", "close": 1.0}}}}]  # fmt: skip
    got = sc.inputs_from_stored(rows)
    assert got is not None and sc.MOMENTUM_POINTS_KEY not in got[0].metrics and got[0].metrics["momentum_echo"] == 0.1
