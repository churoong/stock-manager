"""백테스트 국내·미국 예약을 cron 마다 따로 따라잡는다 (docs/infra.md 25.1437, docs/factors.md 12.31 P1)."""

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
지금 = datetime(2026, 11, 4, 12, 27, tzinfo=UTC)  # 4일 keepalive


def _돌림(tmp_path: Path, 있는것: list[str]) -> tuple[list[dict], str]:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "backtest.yml").write_text(BT, encoding="utf-8")
    보냄: list[dict] = []

    def call(method, url, token, body=None):  # noqa: ANN001, ANN202
        if method == "GET":
            return 200, json.dumps({"workflow_runs": [{"display_title": f"백테스트 예약 {c}"} for c in 있는것]}).encode()
        보냄.append(body)
        return 204, b""

    return 보냄, sc.run("o/r", "tok", call, 지금, tmp_path)


def test_실행_이름에_cron_을_싣는다() -> None:
    assert sc.per_cron(BT)
    assert "BACKTEST_TRIGGER:" in BT
    assert 'os.environ.get("BACKTEST_TRIGGER")' in (ROOT / "batch" / "jobs" / "backtest.py").read_text(encoding="utf-8")


def test_국내만_빠지면_국내를_깨운다(tmp_path: Path) -> None:
    """예전엔 가장 늦은 미국 cron 만 봐서 미국이 돌았으면 국내를 다시 깨우지 않았다"""
    보냄, out = _돌림(tmp_path, [US])
    assert 보냄 == [{"ref": "main", "inputs": {"missed_schedule": KR}}] and "깨움 1개" in out


def test_둘_다_빠지면_국내_하나만_다음_회에_미국(tmp_path: Path) -> None:
    보냄, _ = _돌림(tmp_path, [])
    assert 보냄 == [{"ref": "main", "inputs": {"missed_schedule": KR}}]  # 같은 동시 실행 그룹 — 한 번에 하나
    보냄, _ = _돌림(tmp_path / "2", [KR])
    assert 보냄 == [{"ref": "main", "inputs": {"missed_schedule": US}}]


def test_둘_다_돌았으면_깨우지_않는다(tmp_path: Path) -> None:
    보냄, out = _돌림(tmp_path, [KR, US])
    assert 보냄 == [] and "깨움 0개" in out
