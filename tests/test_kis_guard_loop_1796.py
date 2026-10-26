"""장중 감시를 한 잡 안에서 10분마다 (docs/infra.md 25.1796).

2026-10-26 첫 장중 날 10분 예약(`3-59/10`)이 09:03~09:33 네 회차 모두 시작하지 않았다 — Actions 예약 지연. 국내 일일 배치가
깨우고, 한 잡이 장 끝까지 깨어 회차마다 `intraday_guard` 를 따로 된 프로세스로 부른다. 잡 상한이면 스스로 다시 깨운다.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("kis_guard_loop", ROOT / "scripts" / "kis_guard_loop.py")
gl = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(gl)  # type: ignore[union-attr]

창 = (datetime(2026, 10, 26, 0, 5, tzinfo=UTC), datetime(2026, 10, 26, 6, 15, tzinfo=UTC))  # 09:05~15:15 KST


def t(hm: str, s: int = 0) -> datetime:
    h, m = map(int, hm.split(":"))
    return datetime(2026, 10, 26, h, m, s, tzinfo=UTC)


def test_회차는_창_시작부터_10분_눈금_끝_포함() -> None:
    assert gl.next_slot(t("23:30") - timedelta(days=1), *창) == t("00:05")  # 08:30 KST 에 깨면 09:05 까지 기다린다
    assert gl.next_slot(t("00:05"), *창) == t("00:05")
    assert gl.next_slot(t("00:05", 1), *창) == t("00:15")
    assert gl.next_slot(t("06:15"), *창) == t("06:15")  # 창 끝(15:15)도 돈다
    assert gl.next_slot(t("06:15", 1), *창) is None


def test_창_끝_눈금은_앞당겨_띄워_감시가_시간_밖으로_건너뛰지_않는다() -> None:
    """하위 프로세스가 뜨는 몇 초 사이 창 끝을 넘겨 15:15 회차가 "시간 밖" 이 됐다(25.1796 교차검증 [권고])"""
    from batch.jobs import intraday_guard as ig

    끝 = 창[1]
    assert gl.run_at(끝, 끝) == 끝 - gl.END_MARGIN
    assert gl.run_at(t("06:05"), 끝) == t("06:05")  # 다른 눈금은 그대로
    assert ig.due(gl.run_at(끝, 끝) + timedelta(seconds=5)) is None  # 파이썬이 5초 걸려 떠도 창 안
    assert ig.due(끝 + timedelta(seconds=5)) == "장중 감시 시간 밖"  # 앞당기지 않으면 건너뛴다


def test_실제_달력의_감시_창() -> None:
    def w(iso: str):  # noqa: ANN202
        return gl._window(datetime.fromisoformat(iso).replace(tzinfo=UTC))

    assert w("2026-10-25T23:35") == 창  # 08:35 KST(전날 UTC) — KST 날짜로 읽는다
    assert w("2026-10-25T03:00") is None  # 일요일
    assert w("2026-11-19T00:40") == (datetime(2026, 11, 19, 1, 5, tzinfo=UTC), datetime(2026, 11, 19, 7, 15, tzinfo=UTC))
    assert w("2027-01-04T00:40")[0] == datetime(2027, 1, 4, 1, 5, tzinfo=UTC)  # 새해 첫 거래일 10:00 개장


def test_첫_회차는_창_안이면_바로_그_뒤로는_같은_눈금을_두_번_돌지_않는다() -> None:
    시작 = t("02:07")
    assert gl.plan(t("02:07"), 시작, *창) == ("run", t("02:07"))  # 이어 받은 실행·늦게 깬 실행 — 11:15 까지 비지 않게
    assert gl.plan(t("02:07", 20), 시작, *창, t("02:07")) == ("run", t("02:15"))
    # 회차가 눈금에 딱 맞게 끝나도 같은 눈금은 다시 돌지 않는다
    assert gl.plan(t("02:15"), 시작, *창, t("02:15")) == ("run", t("02:25"))
    # 회차가 길어 눈금을 넘기면 밀린 것은 건너뛴다
    assert gl.plan(t("02:38"), 시작, *창, t("02:15")) == ("run", t("02:45"))
    assert gl.plan(t("06:15", 30), 시작, *창, t("06:15")) == ("done", None)


def test_잡_상한이면_새_회차를_열지_않고_잇는다() -> None:
    시작 = t("23:35") - timedelta(days=1)  # 08:35 KST 에 깸
    끝 = 시작 + gl.BUDGET
    assert gl.plan(끝 - timedelta(minutes=3), 시작, *창, 끝 - timedelta(minutes=10)) == ("run", 끝 - timedelta(minutes=0))
    assert gl.plan(끝 + timedelta(seconds=1), 시작, *창, 끝) == ("chain", None)
    # 330분 + 회차 하나(7분) + 설치가 잡 상한 안에 든다
    data = yaml.safe_load((ROOT / ".github/workflows/kis-guard.yml").read_text(encoding="utf-8"))
    assert gl.BUDGET + timedelta(minutes=7 + 5) <= timedelta(minutes=data["jobs"]["run"]["timeout-minutes"]) <= timedelta(
        minutes=360)


def _돌림(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, 창값, 시계: list[datetime], rcs: list[int]):  # noqa: ANN001, ANN202
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr(gl, "_window", lambda now: 창값)
    부른 = []

    class _Now:
        @staticmethod
        def now(tz=None):  # noqa: ANN001, ANN205
            return 시계.pop(0) if len(시계) > 1 else 시계[0]

    monkeypatch.setattr(gl, "datetime", _Now)
    monkeypatch.setattr(gl.time, "sleep", lambda s: None)

    class _R:
        def __init__(self, rc: int) -> None:
            self.returncode = rc

    def 실행(cmd, check):  # noqa: ANN001, ANN202
        부른.append(cmd)
        return _R(rcs.pop(0) if rcs else 0)

    monkeypatch.setattr(gl.subprocess, "run", 실행)
    return out, 부른


def test_휴장일이면_바로_끝나고_잇지_않는다(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out, 부른 = _돌림(monkeypatch, tmp_path, None, [t("00:10")], [])
    assert gl.main([]) == 0 and 부른 == [] and out.read_text() == "chain=false\n"


def test_회차마다_따로_된_프로세스_실패는_끝까지_돌고_종료코드로(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 시작 · 계획1 · 잠1 · 계획2 · 잠2 · 계획3(창 끝 지남)
    시계 = [t("06:00"), t("06:00"), t("06:00"), t("06:05", 30), t("06:05", 30), t("06:15", 40)]
    out, 부른 = _돌림(monkeypatch, tmp_path, 창, 시계, [1, 0])
    assert gl.main(["--dry-run"]) == 1  # 한 회차 실패 → 잡 실패 → 운영 이슈로 기록
    assert len(부른) == 2 and all(c[-2:] == ["batch.jobs.intraday_guard", "--dry-run"] for c in 부른)
    assert 부른[0][:3] == ["timeout", "--kill-after=30s", gl.RUN_TIMEOUT]
    assert out.read_text() == "chain=false\n"


def test_상한이면_chain_을_남긴다(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    시작 = t("00:20")
    시계 = [시작, 시작, 시작, 시작 + gl.BUDGET + timedelta(minutes=1)]  # 시작 · 계획1 · 잠1 · 계획2
    out, 부른 = _돌림(monkeypatch, tmp_path, 창, 시계, [])
    assert gl.main([]) == 0 and len(부른) == 1
    assert out.read_text() == "chain=false\nchain=true\n"  # 마지막 값이 이긴다


def _쓰기_잡(data: dict) -> set[str]:  # type: ignore[type-arg]
    """actions 쓰기를 가진 잡 — 잡 단위 permissions 가 없으면 최상위를 물려받는다"""
    위 = data.get("permissions") or {}
    return {n for n, j in data["jobs"].items() if (j.get("permissions", 위) or {}).get("actions") == "write"}


def test_워크플로_사슬과_권한() -> None:
    kg = yaml.safe_load((ROOT / ".github/workflows/kis-guard.yml").read_text(encoding="utf-8"))
    dk = yaml.safe_load((ROOT / ".github/workflows/daily-kr.yml").read_text(encoding="utf-8"))
    assert _쓰기_잡(kg) == {"chain"} and _쓰기_잡(dk) == {"guard_start", "keepalive"}  # 패키지 설치하는 본체는 못 쓴다
    트리거 = kg.get(True) or kg["on"]
    assert all("/" not in c["cron"] for c in 트리거["schedule"]), "10분 예약으로 돌아가지 않는다 — 예비 예약만"
    assert "actions" not in (kg.get("permissions") or {}), "패키지를 설치하는 본체에 actions 쓰기를 주지 않는다"
    본체 = "\n".join(str(s.get("run", "")) for s in kg["jobs"]["run"]["steps"])
    assert "python scripts/kis_guard_loop.py $ARGS 2>&1 | python scripts/ops_tee.py run.log" in 본체
    assert '[ "${{ github.event_name }}" = "workflow_dispatch" ] && [ "${{ inputs.loop }}" != "true" ]' in 본체
    사슬 = kg["jobs"]["chain"]
    assert 사슬["needs"] == "run" and "needs.run.outputs.chain == 'true'" in 사슬["if"] and 사슬["if"].startswith("always()")
    assert 사슬["permissions"] == {"actions": "write"}
    assert kg["jobs"]["run"]["outputs"]["chain"] == "${{ steps.guard.outputs.chain }}"
    for job in (사슬, yaml.safe_load((ROOT / ".github/workflows/daily-kr.yml").read_text(encoding="utf-8"))["jobs"]["guard_start"]):
        글 = "\n".join(str(s.get("run", "")) for s in job["steps"])
        assert "actions/workflows/kis-guard.yml/dispatches" in 글 and '"inputs[loop]=true"' in 글
        assert "pip install" not in 글 and job["permissions"] == {"actions": "write"}
    시작글 = "\n".join(str(s.get("run", "")) for s in yaml.safe_load(
        (ROOT / ".github/workflows/daily-kr.yml").read_text(encoding="utf-8"))["jobs"]["guard_start"]["steps"])
    assert '"inputs[dry_run]=false"' in 시작글  # 손 실행 기본값(true)이 아닌 실제 감시로
    # 사슬은 dry_run 을 이어 넘긴다 — 빠지면 기본값(true)이 걸려 오후 회차가 매일 조용히 확인 실행이 된다
    사슬글 = "\n".join(str(s.get("run", "")) for s in 사슬["steps"])
    assert '"inputs[dry_run]=${{ inputs.dry_run == true }}"' in 사슬글
    assert dk["jobs"]["guard_start"]["if"] == "github.event_name != 'workflow_dispatch' || inputs.dry_run != true"
    # 운영 이슈에는 실패·손 한 번 실행만 — 반복 실행도 workflow_dispatch 로 온다
    게시 = next(s for s in kg["jobs"]["run"]["steps"] if "publish_output" in str(s.get("run", "")))
    assert "inputs.loop != true" in 게시["if"] and "failure()" in 게시["if"]


def test_따라잡기는_장중_감시를_깨우지_않는다() -> None:
    _s = importlib.util.spec_from_file_location("schedule_catchup", ROOT / "scripts" / "schedule_catchup.py")
    sc = importlib.util.module_from_spec(_s)  # type: ignore[arg-type]
    _s.loader.exec_module(sc)  # type: ignore[union-attr]
    assert "kis-guard.yml" in sc.SKIP
