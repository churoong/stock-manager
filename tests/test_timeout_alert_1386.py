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


def _배치_분(이름: str) -> int:
    글 = (뿌리 / ".github" / "workflows" / 이름).read_text(encoding="utf-8")
    m = re.search(r"timeout --kill-after=\d+s (\d+)m python -m batch\.jobs\.daily", 글)
    assert m, 이름
    return int(m.group(1))


@pytest.mark.parametrize("이름", ["daily-kr.yml", "daily-us.yml"])
def test_시간_초과_알림의_분은_배치_한도와_같다(이름: str) -> None:
    """25.2113 — 미국 한도를 30→45 로 올리며 알림 인자(`--notify-timeout N`)가 옛 숫자로 남으면 "30분 안에 끝나지 않아" 로
    틀린 글이 나간다"""
    글 = (뿌리 / ".github" / "workflows" / 이름).read_text(encoding="utf-8")
    assert re.findall(r"--notify-timeout (\d+)", 글) == [str(_배치_분(이름))]


def test_미국_주_실행은_한도_안에_KIS_예약_마감_전에_끝난다() -> None:
    """25.2113 (11-02 미국 일일 배치가 30분 한도로 죽음 — 10월 내내 26~29분) — 한도를 늘리되, 주 실행(겨울 13:27Z·여름
    12:27Z, `cal.scheduled_for`) 시작 + 배치 한도가 KIS 미국 예약 접수 마감(개장 − `US_RESV_CUTOFF`)보다 앞서야 늦게
    끝난 실행의 KIS 단계가 '마감 뒤' 로 통째 빠지지 않는다(보내는 도중에도 다시 보지만, 한도 자체가 마감을 넘기면 안 된다)"""
    from datetime import date, datetime, timedelta

    from batch.core import calendar as cal
    from batch.jobs import broker_mirror as bm

    분 = timedelta(minutes=_배치_분("daily-us.yml"))
    준비 = timedelta(minutes=5)  # 25.2115 (25.2113 교차검증 note) — 실제 시작은 예약보다 2~3분 늦다
    for 날 in (date(2026, 11, 3), date(2027, 3, 15), date(2027, 7, 13)):  # 겨울·여름 첫 주·한여름
        예약 = cal.scheduled_for("US", 날)
        개장 = cal.session_open_utc("US", 날)
        assert 예약 is not None and 개장 is not None, 날
        시작 = datetime.fromisoformat(예약) + 준비  # 파이썬은 dispatch 큐·checkout·설치·사본 풀기 뒤에 시작한다
        assert 시작 + 분 <= 개장 - bm.US_RESV_CUTOFF, (날, 시작, 개장)


def test_웹_KIS_조회의_배치_도는_중_창은_가장_큰_한도_더하기_5분() -> None:
    """25.2115 (25.2113 교차검증 [권고]) — 웹 KIS모의투자 열 때 조회(`web/lib/kisVts.ts` `BATCH_RUNNING_WINDOW_MS`)가 45분으로
    남아 미국 한도 45분과 같아졌다(여유 0). 창이 한도보다 짧으면 오래 도는 배치 끝무렵에 웹 조회가 끼어 같은 앱키
    초당 한도로 배치 주문이 거부된다(25.1499·1799 꼴). 한도를 또 올릴 때 이 값이 함께 움직이게 묶는다"""
    글 = (뿌리 / "web" / "lib" / "kisVts.ts").read_text(encoding="utf-8")
    m = re.search(r"BATCH_RUNNING_WINDOW_MS = (\d+) \* 60_000", 글)
    assert m
    assert int(m.group(1)) >= max(_배치_분("daily-kr.yml"), _배치_분("daily-us.yml")) + 5
