"""위험판의 열린 위험 — 보유가 모두 손절선까지 내려 팔리면 잃는 몫 (docs/desk.md 6장, docs/infra.md 25.2278).

docs/roadmap.md 1장 #4 1단계. 표시만 한다 — 주문·사이징을 바꾸지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from batch.services import desk

손절 = {"short": -7.0, "mid": -15.0, "long": -25.0}


def _판(계좌: str, 종목: list[dict], 자산: float = 1_000_000) -> dict:
    return desk.risk_board({계좌: {"equity": 자산, "holdings": 0, "positions": 종목}}, {}, 손절)[0]


def test_손으로_센_열린_위험() -> None:
    r = _판("A", [
        {"name": "가", "horizon": "short", "avg_price": 100.0, "close": 94.5, "weight_pct": 40.0},  # 손절가 93
        {"name": "나", "horizon": "mid", "avg_price": 200.0, "close": 220.0, "weight_pct": 20.0},  # 손절가 170
        {"name": "다", "horizon": None, "avg_price": 100.0, "close": 100.0, "weight_pct": 10.0},  # 기간 없음 → 장기 75
    ])  # fmt: skip
    기대 = 40.0 * (1 - 93 / 94.5) + 20.0 * (1 - 170 / 220) + 10.0 * (1 - 75 / 100)
    assert r["open_risk_pct"] == pytest.approx(기대) and 기대 == pytest.approx(7.6803751803)
    assert "open_risk_skipped" not in r


def test_이미_손절선_밑인_보유는_0_이다() -> None:
    """더 내릴 몫이 아니라 팔릴 차례다 — 음수로 깎지 않는다"""
    r = _판("A", [
        {"name": "가", "horizon": "short", "avg_price": 100.0, "close": 90.0, "weight_pct": 30.0},  # 선(93) 밑
        {"name": "나", "horizon": "short", "avg_price": 100.0, "close": 93.0, "weight_pct": 30.0},  # 선 위에 딱
        {"name": "다", "horizon": "short", "avg_price": 100.0, "close": 100.0, "weight_pct": 10.0},
    ])  # fmt: skip
    assert r["open_risk_pct"] == pytest.approx(10.0 * 0.07)


def test_못_잰_보유는_빼고_수를_알린다() -> None:
    r = _판("A", [
        {"name": "가", "horizon": "short", "avg_price": 100.0, "close": 100.0, "weight_pct": 10.0},
        {"name": "나", "horizon": "short", "avg_price": None, "close": 100.0, "weight_pct": 10.0},
        {"name": "다", "horizon": "short", "avg_price": 100.0, "close": None, "weight_pct": 10.0},
        {"name": "라", "horizon": "short", "avg_price": 100.0, "close": 100.0, "weight_pct": None},
        {"name": "마", "horizon": "short", "avg_price": 100.0, "close": 0.0, "weight_pct": 10.0},
    ])  # fmt: skip
    assert r["open_risk_pct"] == pytest.approx(0.7) and r["open_risk_skipped"] == 4
    # 손절 가까움은 비중이 없어도 그대로 잰다(예전과 같다)
    근처 = _판("A", [{"name": "라", "horizon": "short", "avg_price": 100.0, "close": 94.0, "weight_pct": None}])
    assert [x["name"] for x in 근처["near_stop"]] == ["라"]
    # 하나도 못 재면 0 이 아니라 값 없음
    assert 근처["open_risk_pct"] is None and 근처["open_risk_skipped"] == 1


def test_보유가_없으면_0_이다() -> None:
    r = _판("A", [])
    assert r["open_risk_pct"] == 0.0 and "open_risk_skipped" not in r


@pytest.mark.parametrize("계좌", ["S", "T", "X", "Y"])
def test_손절선으로_재지_않는_계좌는_값이_없다(계좌: str) -> None:
    r = _판(계좌, [{"name": "가", "horizon": "short", "avg_price": 100.0, "close": 100.0, "weight_pct": 50.0}])
    assert r["open_risk_pct"] is None and "open_risk_skipped" not in r and r["near_stop_note"]


@pytest.mark.parametrize("계좌", ["H", "ZL", "ZM", "R1"])
def test_core_매도_규칙을_쓰는_계좌는_잰다(계좌: str) -> None:
    r = _판(계좌, [{"name": "가", "horizon": "long", "avg_price": 100.0, "close": 100.0, "weight_pct": 50.0}])
    assert r["open_risk_pct"] == pytest.approx(12.5)


def test_손절_가까움과_같은_선을_쓴다() -> None:
    """설정으로 손절선이 바뀌면 열린 위험도 같이 움직인다 — 선을 따로 갖지 않는다"""
    종목 = [{"name": "가", "horizon": "short", "avg_price": 100.0, "close": 100.0, "weight_pct": 100.0}]
    r = desk.risk_board({"A": {"equity": 1, "holdings": 1, "positions": 종목}}, {}, {**손절, "short": -10.0})[0]
    assert r["open_risk_pct"] == pytest.approx(10.0)


def test_문서가_식과_한계를_적는다() -> None:
    글 = (Path(__file__).resolve().parent.parent / "docs" / "desk.md").read_text()
    for 조각 in ("**열린 위험**(`open_risk_pct`, 25.2278", "Σ 비중(%) × max(0, 1 − 손절가 ÷ 종가)",
               "손절가 = 평균단가 × (1 + 그 기간 손절 %)", "**아래쪽 한계다**", "`open_risk_skipped`", "주문·사이징을 바꾸지 않는다"):  # fmt: skip
        assert 조각 in 글, 조각
