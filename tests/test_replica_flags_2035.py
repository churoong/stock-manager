"""시세 사본 맞추기 깃발이 워크플로마다 맞는가 (25.2035, 25.2032 교차검증 [권고]).

- 예전 사본(시가 열 없음) 다시 만들기(`--ohl-rebuild`)는 지표 작업 하나만 — 빠지면 다시 만들기가 영영 안 일어나고
  (시가 질의가 늘 Turso), 15분 작업에 붙으면 잘려 캐시에 못 넣고 주마다 2,100만 행을 다시 읽는다
- 일일 배치·백테스트는 사본을 처음부터 만들지 않는다(`--no-build`, 25.1660)
- 지표 작업의 맞추기 단계는 상한을 두고 넘으면 넘어간다(계산 단계를 지킨다)
"""

from __future__ import annotations

from pathlib import Path

import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _맞추기_단계() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for p in sorted(WF.glob("*.yml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        for job in (data.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if "price_replica.py sync" in str(step.get("run") or ""):
                    out.setdefault(p.name, []).append(step)
    return out


def test_시가_열_다시_만들기는_지표_작업만() -> None:
    단계 = _맞추기_단계()
    assert set(단계) >= {"metrics.yml", "signal-outcomes.yml", "valuation-bands.yml", "daily-kr.yml", "daily-us.yml",
                         "backtest.yml"}, sorted(단계)  # fmt: skip
    깃발 = sorted(n for n, ss in 단계.items() for s in ss if "--ohl-rebuild" in s["run"])
    assert 깃발 == ["metrics.yml"]


def test_일일_배치와_백테스트는_사본을_처음부터_만들지_않는다() -> None:
    단계 = _맞추기_단계()
    for n in ("daily-kr.yml", "daily-us.yml", "backtest.yml"):
        assert 단계[n] and all("--no-build" in s["run"] for s in 단계[n]), n


def test_지표_작업의_맞추기는_상한을_두고_넘으면_넘어간다() -> None:
    (s,) = _맞추기_단계()["metrics.yml"]
    assert s.get("continue-on-error") is True and 0 < int(s.get("timeout-minutes") or 0) < 60
