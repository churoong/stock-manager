"""25.1660 — 따라잡기가 취소·실패·시간초과로 끝난 예약 실행을 "있음" 으로 보지 않는다 (11-03 판정 실행 감사 [필수] 4)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import schedule_catchup as sc  # noqa: E402

BT = (ROOT / ".github" / "workflows" / "backtest.yml").read_text(encoding="utf-8")
KR, US = "11 17 3 * *", "11 19 3 * *"
지금 = datetime(2026, 11, 4, 12, 27, tzinfo=UTC)


def _돌림(tmp_path: Path, 실행: list[tuple[str, str, str | None]]) -> tuple[list[dict], str]:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "backtest.yml").write_text(BT, encoding="utf-8")
    보냄: list[dict] = []

    def call(method, url, token, body=None):  # noqa: ANN001, ANN202
        if method == "GET":
            return 200, json.dumps({"workflow_runs": [
                {"display_title": f"백테스트 예약 {c}", "status": st, "conclusion": cc} for c, st, cc in 실행]}).encode()
        보냄.append(body)
        return 204, b""

    return 보냄, sc.run("o/r", "tok", call, 지금, tmp_path)


def test_취소된_국내와_시간초과_미국은_빠진_것이다(tmp_path: Path) -> None:
    보냄, out = _돌림(tmp_path, [(KR, "completed", "cancelled"), (US, "completed", "timed_out")])
    assert 보냄 == [{"ref": "main", "inputs": {"missed_schedule": KR}}] and "깨움 1개" in out


def test_돌고_있거나_성공했거나_스스로_건너뛴_것은_있음(tmp_path: Path) -> None:
    보냄, _ = _돌림(tmp_path, [(KR, "in_progress", None), (US, "completed", "success")])
    assert 보냄 == []
    # 한 번 취소됐어도 다시 깨운 실행이 돌고 있으면 깨우지 않는다
    보냄, _ = _돌림(tmp_path / "2", [(KR, "completed", "cancelled"), (KR, "queued", None), (US, "completed", "success")])
    assert 보냄 == []


def test_일을_못_한_실행이_두_번이면_더_깨우지_않고_실패로(tmp_path: Path) -> None:
    보냄, out = _돌림(tmp_path, [(KR, "completed", "failure"), (KR, "completed", "failure"), (US, "completed", "success")])
    assert 보냄 == [] and "실패" in out


def test_국내를_더_깨우지_않아도_미국은_본다(tmp_path: Path) -> None:
    """25.1661 — 국내가 두 번 일을 못 한 달에 미국 예약이 빠졌으면 미국을 깨운다(예전엔 국내 "실패" 에서 멈췄다)."""
    보냄, out = _돌림(tmp_path, [(KR, "completed", "failure"), (KR, "completed", "failure")])
    assert 보냄 == [{"ref": "main", "inputs": {"missed_schedule": US}}] and "깨움 1개" in out
    보냄, out = _돌림(tmp_path / "2", [(KR, "completed", "failure"), (KR, "completed", "failure"),
                                       (US, "completed", "timed_out")])  # fmt: skip
    assert 보냄 == [{"ref": "main", "inputs": {"missed_schedule": US}}]
