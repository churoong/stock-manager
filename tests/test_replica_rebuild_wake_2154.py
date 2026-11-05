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
    assert any(s.get("id") == "replica" for s in jobs["run"]["steps"])
    깨움 = jobs["replica_rebuild"]
    assert 깨움["needs"] == "run" and "always()" in 깨움["if"] and "needs.run.outputs.replica_empty == 'true'" in 깨움["if"]
    # 쓰기 권한은 이 잡에만, actions 하나만 — 본체는 바깥 패키지를 설치해 돈다
    assert 깨움["permissions"] == {"actions": "write"}
    assert "actions" not in (wf.get("permissions") or {}) and "permissions" not in jobs["run"]
    글 = " ".join(s.get("run", "") for s in 깨움["steps"])
    assert "workflows/metrics.yml/dispatches" in 글 and "inputs[replica_only]=true" in 글
    assert not any(s.get("uses", "").startswith("actions/setup-python") for s in 깨움["steps"])  # 설치 없음


def test_지표_워크플로의_사본만_입력은_계산을_건너뛴다() -> None:
    wf = yaml.safe_load((뿌리 / "metrics.yml").read_text(encoding="utf-8"))
    입력 = wf[True]["workflow_dispatch"]["inputs"]  # yaml 은 'on' 을 참으로 읽는다
    assert 입력["replica_only"]["type"] == "boolean" and 입력["replica_only"]["default"] is False
    단계 = {s.get("name"): s for s in wf["jobs"]["run"]["steps"]}
    assert 단계["계산"]["if"] == "${{ !inputs.replica_only }}"
    assert "--no-build" not in 단계["시세 사본 맞추기"]["run"]  # 비었으면 진도 문을 거쳐 만든다
    assert rep.PRICE_WRITER_JOBS  # 사본 모듈이 그대로 있다
