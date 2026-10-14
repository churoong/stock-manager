"""점수 보정표 — 날짜 평균을 뺀 ρ 를 기록만 (docs/infra.md 25.1359, 23회차 P2)."""

from __future__ import annotations

from dataclasses import dataclass, field

from batch.services import calibration as cb


@dataclass
class _O:
    stock_id: int
    as_of_date: str
    rets: dict = field(default_factory=dict)


def _자료() -> tuple[list[_O], dict]:
    """두 날짜(수익은 2진 소수로 딱 떨어지는 값 — 날짜 평균을 빼면 동률이 정확히 같아야 한다). 날마다 점수와 수익은 무관(순서가 엇갈림), 그런데 높은 점수는 시장이 오른 날에 몰렸다."""
    outs, scores = [], {}
    for i in range(20):  # 오른 날: 점수 70~89, 수익 +50% 근처에서 점수와 무관하게 흔들림
        outs.append(_O(i, "2026-09-01", {20: 0.5 + (0.125 if i % 2 else -0.125)}))
        scores[(i, "2026-09-01")] = 70.0 + i
    for i in range(20, 40):  # 내린 날: 점수 40~59, 수익 −50% 근처
        outs.append(_O(i, "2026-09-02", {20: -0.5 + (0.125 if i % 2 else -0.125)}))
        scores[(i, "2026-09-02")] = 20.0 + i
    return outs, scores


def test_날짜를_섞으면_좋아_보이고_빼면_사라진다() -> None:
    outs, scores = _자료()
    원 = cb.calibrate(cb.pairs_for(outs, scores, 20), 20)
    뺌 = cb.demeaned_check(outs, scores, 20)
    assert 원.rho is not None and 원.rho > 0.5
    assert 뺌["rho_demeaned"] is not None and abs(뺌["rho_demeaned"]) < 0.2
    assert (뺌["n"], 뺌["n_dates"], 뺌["n_stocks"]) == (40, 2, 40)


def test_하루에_하나뿐인_날은_빼고_적으면_None() -> None:
    outs = [_O(1, "2026-09-01", {20: 0.1}), _O(2, "2026-09-02", {20: 0.2}), _O(2, "2026-09-02", {20: 0.2})]
    scores = {(1, "2026-09-01"): 50.0, (2, "2026-09-02"): 60.0}
    뺌 = cb.demeaned_check(outs, scores, 20)
    assert (뺌["n"], 뺌["n_dates"], 뺌["rho_demeaned"]) == (0, 0, None)


def test_보정표_payload_는_그대로이고_곁에_붙는다() -> None:
    from pathlib import Path

    원문 = (Path(__file__).resolve().parents[1] / "batch/jobs/signal_outcomes.py").read_text()
    assert '"demeaned": cb.demeaned_check(outcomes, scores, w)' in 원문
    outs, scores = _자료()
    assert "demeaned" not in cb.calibrate(cb.pairs_for(outs, scores, 20), 20).as_payload()
