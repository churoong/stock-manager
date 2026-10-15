"""시간 제한으로 강제 종료된 일일 배치도 실패 알림을 보낸다 (docs/infra.md 25.1386, 배치 실패 감사).

예전에는 잡 시간 제한(국내 15분)이 프로세스를 죽이면 파이썬이 `_notify_failure` 에 닿지 못해 알림이 없었고,
기록은 `running` 으로 남아 6시간 뒤 건너뜀으로 닫혔다. 이제 배치 단계를 `timeout` 으로 끊고, 124·137 이면
다음 단계가 `--notify-timeout` 으로 알림만 보낸다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from batch.core import db
from batch.jobs import daily

yaml = pytest.importorskip("yaml", reason="PyYAML 이 있어야 워크플로를 검증한다")

뿌리 = Path(__file__).resolve().parent.parent


def test_사유는_리포트를_안_보냈다고_단정하지_않는다() -> None:
    글 = daily.timeout_text(15)
    assert "15분 시간 제한" in 글
    assert "보냈는지는 알 수 없습니다" in 글
    assert f"{db.STALE_RUN_HOURS}시간 뒤" in 글


def test_DB_를_못_열어도_알림을_낸다(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def 실패() -> None:
        raise RuntimeError("연결 불가")

    monkeypatch.setattr(daily, "TursoClient", 실패)
    daily.notify_timeout("KR", 15, dry_run=True)
    출력 = capsys.readouterr().out
    assert "실패 알림 (dry-run)" in 출력
    assert "15분 시간 제한에 걸려 강제 종료됐습니다" in 출력
    assert "마지막 성공 기록을 읽지 못했습니다" in 출력
    assert daily.job_name("KR") in 출력


def test_알림만_보내고_배치는_돌리지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    불림: list[str] = []
    monkeypatch.setattr(daily, "run", lambda *a, **k: 불림.append("run") or 0)
    monkeypatch.setattr(daily, "notify_timeout", lambda m, n, dry_run: 불림.append(f"timeout {m} {n} {dry_run}"))
    monkeypatch.setattr("sys.argv", ["daily", "--market", "us", "--notify-timeout", "30", "--dry-run"])
    assert daily.main() == 0
    assert 불림 == ["timeout US 30 True"]


@pytest.mark.parametrize("이름", ["daily-kr.yml", "daily-us.yml"])
def test_워크플로가_시간_제한을_배치_단계에서_건다(이름: str) -> None:
    글 = (뿌리 / ".github" / "workflows" / 이름).read_text(encoding="utf-8")
    잡 = yaml.safe_load(글)["jobs"]["run"]
    단계 = {s.get("id") or s.get("name"): s for s in 잡["steps"]}
    배치 = 단계["batch"]["run"]
    m = re.search(r"timeout --kill-after=\d+s (\d+)m python -m batch\.jobs\.daily", 배치)
    assert m, "배치 줄이 timeout 으로 감싸여 있지 않다"
    분 = int(m.group(1))
    # 잡 시간 제한이 배치보다 넉넉해야 뒤의 알림 단계가 돈다
    assert 잡["timeout-minutes"] >= 분 + 3
    assert '"$rc" = "124"' in 배치 and '"$rc" = "137"' in 배치
    assert "timed_out=true" in 배치
    # 실패 종료코드는 그대로 남긴다 — did_work 를 적은 뒤 끝낸다
    assert 배치.rstrip().endswith('exit "$rc"')
    알림 = [s for s in 잡["steps"] if "timed_out == 'true'" in str(s.get("if", ""))]
    assert len(알림) == 1, "시간 초과 알림 단계가 없다"
    assert f"--notify-timeout {분}" in 알림[0]["run"], "알림이 적는 분이 실제 제한과 다르다"
    assert "always()" in 알림[0]["if"]
    # 알림 단계가 이슈 내보내기보다 앞서야 그 출력이 운영 기록에 실린다
    이름들 = [s.get("name", "") for s in 잡["steps"]]
    assert 이름들.index(알림[0]["name"]) < next(i for i, n in enumerate(이름들) if n.startswith("기록을 이슈로"))
