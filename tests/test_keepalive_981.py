"""예약 워크플로 살려 두기 (docs/infra.md 25.981).

공개 저장소에서는 60일 무활동이면 예약 워크플로가 꺼진다. 일일 배치가 매일 다시 켠다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import keepalive_workflows as ka  # noqa: E402


def test_무활동으로_꺼진_것은_되살리고_사람이_끈_것은_두다() -> None:
    wfs = [
        {"id": 1, "path": ".github/workflows/etf.yml", "state": "disabled_inactivity"},
        {"id": 2, "path": ".github/workflows/daily-kr.yml", "state": "active"},
        {"id": 3, "path": ".github/workflows/probe.yml", "state": "disabled_manually"},
        {"id": 4, "path": ".github/workflows/x.yml", "state": "disabled_fork"},
    ]
    revive, touch = ka.plan(wfs)
    assert [w["id"] for w in revive] == [1]
    assert [w["id"] for w in touch] == [2]


def test_토큰이_없으면_조용히_건너뛴다(monkeypatch, capsys) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert ka.main() == 0
    assert "건너뜀" in capsys.readouterr().out


def test_목록을_못_읽어도_배치를_죽이지_않는다(monkeypatch, capsys) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(ka, "_call", lambda *a: (_ for _ in ()).throw(OSError("net")))
    assert ka.main() == 0
    assert "실패" in capsys.readouterr().out


def test_되살린_것과_실패를_찍는다(monkeypatch, capsys) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    body = b'{"workflows": [{"id": 1, "path": ".github/workflows/etf.yml", "state": "disabled_inactivity"},' \
           b' {"id": 2, "path": ".github/workflows/daily-kr.yml", "state": "active"}]}'
    calls = []

    def fake(method, url, token):
        calls.append((method, url))
        if method == "GET":
            return 200, body
        return (204, b"") if "/1/" in url else (403, b"")

    monkeypatch.setattr(ka, "_call", fake)
    assert ka.main() == 0
    out = capsys.readouterr().out
    assert "되살림 1개: etf.yml" in out and "실패 1개: daily-kr.yml(403)" in out
    assert ("PUT", "https://api.github.com/repos/o/r/actions/workflows/1/enable") in calls


def test_일일_배치가_매일_살려_둔다() -> None:
    """cron-job.org 가 매 거래일 깨우는 둘이 다른 예약 워크플로를 켠다. 둘 다 빠지면 두 달 뒤 조용히 멈춘다."""
    for name in ("daily-kr.yml", "daily-us.yml"):
        data = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
        assert (data.get("permissions") or {}).get("actions") == "write", f"{name}: actions: write 가 없으면 켜기 요청이 403"
        steps = data["jobs"]["run"]["steps"]
        살림 = [s for s in steps if "keepalive_workflows.py" in str(s.get("run", ""))]
        assert 살림, f"{name}: 예약 워크플로 살려 두기 단계가 없다"
        assert "cancelled" in str(살림[0].get("if", "")), f"{name}: 배치가 실패해도 살려 두기는 돌아야 한다"
