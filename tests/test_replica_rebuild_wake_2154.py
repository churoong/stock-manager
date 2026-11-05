"""25.2154 — 일일 배치가 시세 사본이 빈 채 끝나면 처음 만들기를 바로 깨운다.

11-04 정리된 기록 하나로 사본이 비워지자 처음 만들기는 주간 작업(화 02:23 KST)까지 미뤄졌고, 그 사이 11-05 국내 배치가
Turso 에서 읽다 15분 상한에 두 번 걸렸다(25.2150)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from batch.core import price_replica as rep
from tests.test_price_replica import _원격
from tests.test_replica_ohl_2027 import _스크립트

뿌리 = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _출력(path: Path) -> dict[str, str]:
    return dict(줄.split("=", 1) for 줄 in path.read_text(encoding="utf-8").splitlines() if "=" in 줄)


@pytest.mark.parametrize(("만들까", "빔"), [(False, "true"), (True, "false")])
def test_스크립트는_사본이_비었는지_내보낸다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, 만들까: bool, 빔: str) -> None:
    원격 = _원격()
    원격.reads = type("R", (), {"total": 0})()
    원격.close = lambda: None
    mod = _스크립트()
    monkeypatch.setattr(mod.backend, "resolved_backend", lambda: "turso")
    monkeypatch.setattr(mod.backend, "TursoClient", lambda use_replica=False: 원격)
    monkeypatch.setattr(mod, "_gate_allows_build", lambda _r: (True, "시험"))
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    인자 = ["x", "sync", "--path", str(tmp_path / "p.db")] + ([] if 만들까 else ["--no-build"])
    monkeypatch.setattr("sys.argv", 인자)
    assert mod.main() == 0
    assert _출력(out)["empty"] == 빔


@pytest.mark.parametrize("파일", ["daily-kr.yml", "daily-us.yml"])
def test_일일_배치는_빈_사본이면_사본만_맞추기를_깨운다(파일: str) -> None:
    wf = yaml.safe_load((뿌리 / 파일).read_text(encoding="utf-8"))
    jobs = wf["jobs"]
    assert jobs["run"]["outputs"]["replica_empty"] == "${{ steps.replica.outputs.empty }}"
    [맞추기] = [s for s in jobs["run"]["steps"] if s.get("id") == "replica"]
    # 배치가 시간 초과로 실패해도 돈다 — 아니면 `empty` 가 안 나와 깨움이 없다(25.2155, 교차검증 [권고])
    assert "!cancelled()" in 맞추기["if"] and "success()" not in 맞추기["if"] and "failure()" not in 맞추기["if"]
    깨움 = jobs["replica_rebuild"]
    assert 깨움["needs"] == "run" and "always()" in 깨움["if"] and "needs.run.outputs.replica_empty == 'true'" in 깨움["if"]
    # 쓰기 권한은 이 잡에만, actions 하나만 — 본체는 바깥 패키지를 설치해 돈다
    assert 깨움["permissions"] == {"actions": "write"}
    assert "actions" not in (wf.get("permissions") or {}) and "permissions" not in jobs["run"]
    글 = " ".join(s.get("run", "") for s in 깨움["steps"])
    assert "workflows/metrics.yml/dispatches" in 글 and "inputs[replica_only]=true" in 글
    # 지표가 이미 돌거나 기다리면 깨우지 않는다 — 기다리던 주간 계산을 취소하지 않게(25.2155)
    assert "status=$st" in 글 and "in_progress queued" in 글 and 'if [ "$busy" -gt 0 ]' in 글
    assert not any(s.get("uses", "").startswith("actions/setup-python") for s in 깨움["steps"])  # 설치 없음


def test_지표_워크플로의_사본만_입력은_계산을_건너뛴다() -> None:
    wf = yaml.safe_load((뿌리 / "metrics.yml").read_text(encoding="utf-8"))
    입력 = wf[True]["workflow_dispatch"]["inputs"]  # yaml 은 'on' 을 참으로 읽는다
    assert 입력["replica_only"]["type"] == "boolean" and 입력["replica_only"]["default"] is False
    단계 = {s.get("name"): s for s in wf["jobs"]["run"]["steps"]}
    assert 단계["계산"]["if"] == "${{ !inputs.replica_only }}"
    assert "--no-build" not in 단계["시세 사본 맞추기"]["run"]  # 비었으면 진도 문을 거쳐 만든다
    assert rep.PRICE_WRITER_JOBS  # 사본 모듈이 그대로 있다


def _사본_있음(tmp_path: Path, 원격) -> Path:  # noqa: ANN001
    from datetime import UTC, datetime, timedelta

    path = tmp_path / "p.db"
    conn = rep.open_replica(path)
    rep.sync(원격, conn, backend="turso", allow_build=True, now=datetime.now(UTC) - timedelta(hours=2))
    conn.close()
    return path


@pytest.mark.parametrize(("넓은기록", "지표", "빔", "쓸수있음"), [
    (False, False, "false", "1"),  # 평소 — 일일 배치 뒤 맞추기(--no-build)는 비지 않는다
    (True, False, "true", "0"),    # 넓은 범위로 비움 — 일일 배치는 만들지 않는다(깨움)
    (True, True, "false", "1"),    # 깨운 지표 실행 — 비운 그 자리에서 처음부터 만든다(25.2155 [필수])
])  # fmt: skip
def test_비우는_길과_깨운_지표가_그날_다시_만드는_길(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, 넓은기록: bool,
                                         지표: bool, 빔: str, 쓸수있음: str) -> None:  # fmt: skip
    """25.2155 (25.2154 교차검증 [필수], 재현) — 깨운 지표 실행이 캐시의 옛 사본을 꺼내 비우기만 하고 끝났다"""
    import json
    import sqlite3
    from datetime import UTC, datetime

    원격 = _원격()
    원격.reads = type("R", (), {"total": 0})()
    원격.close = lambda: None
    path = _사본_있음(tmp_path, 원격)
    if 넓은기록:
        원격.execute("INSERT INTO batch_runs (job_name, market, trigger_source, started_at, finished_at, status, step_log)"
                     " VALUES ('daily_us', 'US', 'schedule', ?, ?, 'success', ?)",
                     [datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(),
                      json.dumps({"prices_touched": {"all": True, "min_date": "2020-01-01", "stocks": {}}})])  # fmt: skip
    mod = _스크립트()
    monkeypatch.setattr(mod.backend, "resolved_backend", lambda: "turso")
    monkeypatch.setattr(mod.backend, "TursoClient", lambda use_replica=False: 원격)
    monkeypatch.setattr(mod, "_gate_allows_build", lambda _r: (True, "시험"))
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    인자 = ["x", "sync", "--path", str(path)] + (["--ohl-rebuild"] if 지표 else ["--no-build"])
    monkeypatch.setattr("sys.argv", 인자)
    assert mod.main() == 0
    assert _출력(out)["empty"] == 빔
    assert dict(sqlite3.connect(str(path)).execute("SELECT key, value FROM meta").fetchall())["usable"] == 쓸수있음


def test_신호_성적표처럼_작은_작업은_비운_뒤_다시_만들지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--ohl-rebuild 없는 작업(15분 신호 성적표·밴드)은 2,100만 행 다시 받기가 잘린다(25.2032) — 비우기만"""
    import json
    from datetime import UTC, datetime

    원격 = _원격()
    원격.reads = type("R", (), {"total": 0})()
    원격.close = lambda: None
    path = _사본_있음(tmp_path, 원격)
    원격.execute("INSERT INTO batch_runs (job_name, market, trigger_source, started_at, finished_at, status, step_log)"
                 " VALUES ('daily_us', 'US', 'schedule', ?, ?, 'success', ?)",
                 [datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(),
                  json.dumps({"prices_touched": {"all": True, "min_date": "2020-01-01", "stocks": {}}})])  # fmt: skip
    mod = _스크립트()
    monkeypatch.setattr(mod.backend, "resolved_backend", lambda: "turso")
    monkeypatch.setattr(mod.backend, "TursoClient", lambda use_replica=False: 원격)
    물음: list[int] = []
    monkeypatch.setattr(mod, "_gate_allows_build", lambda _r: (물음.append(1), (True, "시험"))[1])
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr("sys.argv", ["x", "sync", "--path", str(path)])
    assert mod.main() == 0 and 물음 == [] and _출력(out)["empty"] == "true"
