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


@pytest.mark.parametrize("계좌", ["S", "T", "X", "Y"])
def test_손절선으로_재지_않는_계좌는_보유가_없어도_값이_없다(계좌: str) -> None:
    """25.2279 — 단타 T 의 평소 상태(보유 0)·ETF 전술의 현금 구간이 "0.0%"(잴 수 있는데 위험 0)로 보이면 안 된다"""
    assert _판(계좌, [])["open_risk_pct"] is None


def test_손절로_팔지_않는_계좌_D_는_값이_없다() -> None:
    """25.2279 (25.2278 교차검증 [권고]) — D 는 기간초과로만 판다. 선에서 팔리지 않으니 한계가 못 된다"""
    from batch.services import paper as pp

    assert desk.OPEN_RISK_NO_STOP_IDS == ("D",) and "손절" not in pp.SELL_RULES["time"]
    종목 = [{"name": "가", "horizon": "long", "avg_price": 100.0, "close": 76.0, "weight_pct": 50.0},  # −24% ≤ −25 + 2
            {"name": "나", "horizon": "long", "avg_price": None, "close": 80.0, "weight_pct": 50.0}]  # fmt: skip
    r = _판("D", 종목)
    assert r["open_risk_pct"] is None and "open_risk_skipped" not in r
    assert [x["name"] for x in r["near_stop"]] == ["가"]  # 손절 가까움은 예전대로 같은 선으로 보인다
    assert _판("D", [])["open_risk_pct"] is None


def test_손절_없는_매도_원칙의_계좌는_모두_목록에_있다() -> None:
    """25.2280 (25.2279 교차검증 [권고]) — 목록을 계좌 ID 로 박아 두었다. 코드가 아는 계좌 → 매도 원칙(위원회 헌장·
    거장 헌장·핵심/탐색 변형)에서 '손절' 을 따르지 않는 원칙의 계좌 가운데 손절 가까움 글로 가르지 않는 것이 곧 이
    목록이어야 한다 — D 의 원칙이 바뀌거나 새 계좌가 기간초과만으로 팔면 여기서 깨진다"""
    from batch.services import company as co
    from batch.services import core_strategy as cs
    from batch.services import masters
    from batch.services import paper as pp

    원칙: dict[str, set[str]] = {}
    for 계좌, 헌장 in {**co.CHARTERS, **masters.CHARTERS}.items():
        원칙.setdefault(계좌, set()).add(헌장["sell_rule"])
    for v in {**cs.VARIANTS, **cs.EXPLORE_VARIANTS}.values():
        원칙.setdefault(v.account, set()).add(v.sell_rule)
    assert {"A", "C", "D", "E", "H", "G", "B", "R1", "R2", "ZL", "ZM"} <= set(원칙)
    def 없음(r: str) -> bool:
        return pp.SELL_RULES[r] is not None and "손절" not in pp.SELL_RULES[r]

    # 한 곳이라도(any)·모든 곳에서(all) — 둘이 같아야 한다(헌장과 변형이 같은 원칙을 말한다)
    for 고름 in (any, all):
        손절없음 = {a for a, rs in 원칙.items() if 고름(없음(r) for r in rs) and desk._손절_해당없음(a, 1) is None}
        assert 손절없음 == set(desk.OPEN_RISK_NO_STOP_IDS), 고름.__name__


def test_보유_평가가_있는데_종목이_비면_0_이_아니라_모름이다() -> None:
    """보유 글(positions_json)이 깨져 빈 목록으로 읽힌 날 — "위험 0" 으로 보이지 않게"""
    r = desk.risk_board({"A": {"equity": 1_000_000, "holdings": 500_000, "positions": []}}, {}, 손절)[0]
    assert r["open_risk_pct"] is None and r["exposure_pct"] == pytest.approx(50.0)


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
               "손절가 = 평균단가 × (1 + 그 기간 손절 %)", "**아래쪽 한계다**", "`open_risk_skipped`", "주문·사이징을 바꾸지 않는다",
               "손절로 팔지 않는 계좌(D — `OPEN_RISK_NO_STOP_IDS`"):  # fmt: skip
        assert 조각 in 글, 조각
