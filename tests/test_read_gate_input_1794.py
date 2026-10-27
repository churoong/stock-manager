"""손 실행에서만 Turso 읽기 진도 문을 끈다 — 한도 상수는 그대로 (docs/infra.md 25.1794).

2026-10 Turso 읽기가 5억(무료 한도)을 넘었다. 사용자가 이번 달만 한시적으로 결제했고 "배치를 돌려주되 한도는 그대로 유지해줘".
그래서 `db.TURSO_MONTHLY_READ_LIMIT` 은 그대로 두고, 진도 문에 미뤄진 주간 작업을 손 실행(`read_gate=off`)으로만 돌린다.
예약 실행에는 inputs 가 없어 늘 문을 지난다.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from batch.core import db

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
문_끄기_입력 = (
    "universe.yml",
    "financials.yml",
    "metrics.yml",
    "valuation-bands.yml",
    "signal-outcomes.yml",
    "earnings-calendar.yml",
    "refresh-us-adjusted.yml",  # 25.1828 — 빠져 10월 내내 진도 문에 미뤄졌다
)
식 = re.compile(r"\$\{\{ inputs\.read_gate != 'off' && '(\d+)' \|\| '' \}\}")


def _식(raw: str, inputs: dict[str, str] | None) -> str:
    """GitHub 식 `a != b && 'N' || ''` 을 흉내 낸다 — 예약 실행이면 inputs 가 없다(null != 'off')."""
    m = 식.fullmatch(raw)
    assert m, raw
    값 = (inputs or {}).get("read_gate")
    return m.group(1) if 값 != "off" else ""


def test_한도_상수는_그대로() -> None:
    assert db.TURSO_MONTHLY_READ_LIMIT == 500_000_000


def test_예약_실행은_늘_문을_지난다_손_실행_off_만_끈다() -> None:
    for 이름 in 문_끄기_입력:
        data = yaml.safe_load((WORKFLOWS / 이름).read_text(encoding="utf-8"))
        트리거 = data.get(True) or data.get("on")  # PyYAML 은 on 을 True 로 읽는다
        입력 = 트리거["workflow_dispatch"]["inputs"]["read_gate"]
        assert 입력["default"] == "on" and 입력["options"] == ["on", "off"], 이름
        raws = [
            (job.get("env") or {}).get("TURSO_READ_ESTIMATE")
            for job in data["jobs"].values()
            if (job.get("env") or {}).get("TURSO_READ_ESTIMATE")
        ]
        assert raws, 이름
        for raw in raws:
            assert int(_식(raw, None)) > 0, 이름  # 예약
            assert int(_식(raw, {"read_gate": "on"})) > 0, 이름  # 손 실행 기본값
            assert _식(raw, {"read_gate": "off"}) == "", 이름  # 손 실행 문 끄기
