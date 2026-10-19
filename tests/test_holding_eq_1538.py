"""H 회사 가중 대 동일가중 대조 진단 (docs/backtest.md 10장 "가중 대조", docs/factors.md 12.34 31회차 P1,
docs/infra.md 25.1538). 손으로 셀 수 있는 작은 자료."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from batch.services import holding_eq as heq


def _행(sid: int, 점수: float) -> SimpleNamespace:
    return SimpleNamespace(stock_id=sid, total_score=점수, horizon="short", criteria=["c"])


def test_고르기는_운영과_같고_동일가중이면_순서가_바뀐다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.services import report_picks

    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: list(rows))
    # 종목 1 = A·B 추천, 2 = C·D 추천. A 가중 2 면 1 이 위, 동일가중이면 점수(동점 깨기)로 2 가 위
    표 = {"A": {1: 1.0}, "B": {1: 1.0}, "C": {2: 1.0}, "D": {2: 1.0}}
    가중 = {"A": 2.0, "B": 1.0, "C": 1.0, "D": 1.0}
    dv = SimpleNamespace(day="2026-01-05", rows=[_행(1, 50), _행(2, 60)], votes=표, company_weights=가중, picks=[1])
    w, e = heq.day_sets(dv, top_n=1)
    assert (w, e) == ([1], [2])
    # 하루 재료의 고른 것과 다시 낸 값이 다르면 크게 멈춘다
    with pytest.raises(ValueError, match="다르다"):
        heq.day_sets(SimpleNamespace(**{**vars(dv), "picks": [2]}), top_n=1)


def test_근거표가_빈_종목은_고르지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """운영·`CoreMarket.at` 처럼 `with_criteria` 를 거친 행만 — 안 거르면 근거표 없는 종목이 E 에 들어온다"""
    from batch.services import report_picks

    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: [r for r in rows if r.criteria])
    표 = {"A": {1: 1.0, 2: 1.0}, "B": {1: 1.0, 2: 1.0}}
    dv = SimpleNamespace(day="2026-01-05", rows=[_행(1, 50), SimpleNamespace(**{**vars(_행(2, 90)), "criteria": []})],
                         votes=표, company_weights={"A": 1.0, "B": 1.0}, picks=[1])  # fmt: skip
    assert heq.day_sets(dv, top_n=1) == ([1], [1])


def _날(k: int) -> str:
    from datetime import date, timedelta

    return (date(2024, 1, 1) + timedelta(days=k)).isoformat()


def test_요약_손계산() -> None:
    days = [
        (_날(0), [1, 2], [1, 3]),  # 다름: W∖E={2}, E∖W={3} → 5 − 1 = 4
        (_날(1), [1, 2], [1, 2]),  # 같음
        (_날(2), [1, 2], [1, 3]),  # 같은 짝 28일 안 → 되풀이
        (_날(40), [4], [5]),  # 성과 없음 → 빈 날
        (_날(60), [6, 7], [8, 9]),  # (2 + 4)/2 − (1 + 1)/2 = 2
    ]
    성과 = {(_날(0), 2): 5.0, (_날(0), 3): 1.0, (_날(2), 2): 9.0, (_날(2), 3): 0.0, (_날(40), 4): 1.0,
           (_날(60), 6): 2.0, (_날(60), 7): 4.0, (_날(60), 8): 1.0, (_날(60), 9): 1.0}  # fmt: skip
    s = heq.summarize(days, 성과)
    assert (s["days"], s["differ_days"], s["missing_days"], s["repeat_days"], s["n"], s["pairs"]) == (5, 4, 1, 1, 2, 2)
    assert s["differ_pct"] == 80.0
    assert s["mean"] == 3.0 and s["first_half"] == 4.0 and s["second_half"] == 2.0
    assert s["verdict"] == "undecidable"


def _판(n_blocks: int, pairs: int, lower: float | None, upper: float | None, 앞: float, 뒤: float) -> dict:
    return {"n_blocks": n_blocks, "pairs": pairs, "lower": lower, "upper": upper, "first_half": 앞, "second_half": 뒤}


def test_결정_규칙() -> None:
    assert heq.decide(_판(11, 99, 1, 2, 1, 1)) == "undecidable"  # 묶음 12 미만
    assert heq.decide(_판(12, 29, 1, 2, 1, 1)) == "undecidable"  # 짝 30 미만
    assert heq.decide(_판(12, 30, 0.1, 2, 1, 1)) == "keep"
    assert heq.decide(_판(12, 30, 0.1, 2, 1, -0.1)) == "none"  # 뒤 반이 음이면 유지 아님
    assert heq.decide(_판(12, 30, -2, -0.1, -1, -1)) == "equal"
    assert heq.decide(_판(12, 30, -2, -0.1, -1, 0.1)) == "none"
    assert heq.decide(_판(12, 30, -1, 1, 1, 1)) == "none"  # 0 을 걸치면 지금대로


def test_묶음이_차면_하한과_상한을_낸다() -> None:
    # 28일 간격 14 묶음, 짝마다 다른 종목 — 차이가 늘 양(1~2)
    days = [(_날(28 * k), [100 + k], [200 + k]) for k in range(14)]
    성과 = {}
    for k in range(14):
        성과[(_날(28 * k), 100 + k)] = 1.0 + (k % 2)
        성과[(_날(28 * k), 200 + k)] = 0.0
    s = heq.summarize(days, 성과)
    assert s["n_blocks"] == 14 and s["pairs"] == 14 and s["lower"] > 0 and s["upper"] > s["lower"]
    assert s["verdict"] == "undecidable"  # 짝 30 미만
