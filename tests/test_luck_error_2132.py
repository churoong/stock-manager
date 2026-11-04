"""핵심·탐색 실행에서 운 판정(`luck.judge`)이 깨지면 줄마다 실패 표지를 싣는다 (docs/infra.md 25.2132, 25.2106 [참고] ①).

예전엔 `luck` 칸만 빠져 판정판 DSR 칸 까닭이 "metrics_json.luck 없음(25.997 전 기록)" — 옛 기록 탓으로 읽혔다.
25.1956 `monthly_excess_error`·`capacity_error` 와 같은 원칙.
"""

from __future__ import annotations

import json

import pytest

from batch.jobs import core_backtest as cj
from batch.services import luck
from batch.services import scoreboard as scb
from tests.test_core_backtest_job_1534 import _db, _행들, 오늘
from tests.test_scoreboard_hardening_1956 import _실행


def _망(*_a: object, **_k: object) -> None:
    raise RuntimeError("망")


def test_운_판정이_깨지면_줄에_실패_표지(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _db(monkeypatch)
    monkeypatch.setattr(luck, "judge", _망)
    assert cj.run_core("KR", years=1, today=오늘, force=True, variants=("core_b",)) == 0
    (r,) = _행들(mem)
    mj = json.loads(r["metrics_json"])
    assert "luck" not in mj and mj["luck_error"] == "RuntimeError: 망", mj.get("luck_error")


def test_결정_단위만_깨지면_운_판정은_그대로_표지_없음(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.services import core_evidence as ce

    mem = _db(monkeypatch)
    monkeypatch.setattr(ce, "evidence", _망)
    assert cj.run_core("KR", years=1, today=오늘, force=True, variants=("core_b",)) == 0
    (r,) = _행들(mem)
    mj = json.loads(r["metrics_json"])
    assert "luck" in mj and "luck_error" not in mj


def test_판정판은_실패라고_적는다() -> None:
    (줄,) = scb.rows([_실행("core_backtest", {"core_h": None}, luck_error="RuntimeError: 망")])
    assert 줄["dsr"] is None and 줄["missing"]["dsr"] == "운 판정 계산 실패 — RuntimeError: 망", 줄["missing"]["dsr"]
    (옛,) = scb.rows([_실행("core_backtest", {"core_h": None})])
    assert 옛["missing"]["dsr"] == "metrics_json.luck 없음(25.997 전 기록)"
