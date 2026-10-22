"""25.1672 — 백테스트와 함께 돈 스트레스만 실패해도 실행을 실패로 닫지 않는다(따라잡기가 판정 창에 백테스트를 다시 돌리지 않게).
백테스트 단계의 실패는 그대로 실행 실패다 — 그래야 빠진 판정 실행을 따라잡기가 다시 깨운다(25.1660)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = yaml.safe_load((ROOT / ".github" / "workflows" / "backtest.yml").read_text(encoding="utf-8"))


def _단계(job: str, 이름: str) -> dict:
    return next(s for s in WF["jobs"][job]["steps"] if s.get("name") == 이름)


def test_스트레스는_함께_돌_때만_실패를_넘긴다() -> None:
    assert _단계("run", "스트레스").get("continue-on-error") == "${{ env.WHAT != '스트레스' }}"


def test_백테스트_단계의_실패는_실행_실패다() -> None:
    for job, 이름 in (("run", "백테스트"), ("run", "장기 신호 문턱 비교"), ("core", "핵심 H 백테스트")):
        assert "continue-on-error" not in _단계(job, 이름), (job, 이름)
