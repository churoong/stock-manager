"""한 워크플로 안에서 DB 백엔드를 한 번만 고른다 (docs/infra.md 25.472)."""

from __future__ import annotations

from pathlib import Path

import pytest

from batch.core import client as backend

뿌리 = Path(__file__).resolve().parent.parent


def test_고정값이_있으면_복귀_표시를_다시_보지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_BACKEND", "auto")
    monkeypatch.setenv(backend.PIN_ENV, "d1")
    monkeypatch.setattr(backend, "_resolved", None)
    monkeypatch.setattr(backend, "read_return_marker", lambda: pytest.fail("고정됐는데 다시 봤다"))
    assert backend.resolved_backend() == "d1"


def test_고정값이_이상하면_무시하고_스스로_고른다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_BACKEND", "auto")
    monkeypatch.setenv(backend.PIN_ENV, "auto")
    monkeypatch.setattr(backend, "_resolved", None)
    monkeypatch.setattr(backend, "read_return_marker", lambda: True)
    assert backend.resolved_backend() == "turso"


def test_auto_가_아니면_고정값을_보지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_BACKEND", "turso")
    monkeypatch.setenv(backend.PIN_ENV, "d1")
    assert backend.resolved_backend() == "turso"


#: 고정하지 않는 워크플로와 **이유**. 복귀는 도중에 DB 가 바뀌는 것이 목적이고, 따라잡기는 복귀와 같은 줄에 서서(25.463)
#: 도는 동안 복귀 표시가 생기지 않는다
고정_안함 = {
    "turso-return.yml": "1단계가 복귀 표시를 적은 뒤 뒤 단계가 Turso 를 봐야 한다 — 고정하면 D1 에 묶인다",
    "d1-catchup.yml": "복귀와 같은 동시 실행 그룹이라 도는 동안 복귀 표시가 바뀌지 않는다 (25.463)",
    "step0-check.yml": "파이썬 두 줄이 if/else 라 한쪽만 돈다. DB_BACKEND 도 실행 단계 env 에만 있다 (25.481)",
}


def _워크플로들() -> list[str]:
    import re

    import yaml

    나온것 = []
    for p in sorted((뿌리 / ".github" / "workflows").glob("*.yml")):
        글 = p.read_text(encoding="utf-8")
        if "DB_BACKEND" not in 글 or p.name in 고정_안함:
            continue
        for 잡 in yaml.safe_load(글)["jobs"].values():
            # DB 를 쓰는 파이썬만 센다 — 로그 게시(publish_output)와 고정 단계 자신은 빼야 한다 (25.481, 교차검증:
            # 그것까지 세어 파이썬이 하나뿐인 daily-kr·us 가 대상이 됐다). 출력 받기(ops_tee, 25.978)도 DB 를 쓰지
            # 않는다
            패턴 = r"python (?:-m batch\.(?!core\.client)|scripts/(?!publish_output|ops_tee))"
            수 = sum(len(re.findall(패턴, str(s.get("run", "")))) for s in 잡.get("steps", []))
            if 수 >= 2:
                나온것.append(p.name)
                break
    return 나온것


def test_훑기가_비지_않았다() -> None:
    assert len(_워크플로들()) >= 12


@pytest.mark.parametrize("이름", _워크플로들())
def test_파이썬을_여럿_부르는_워크플로는_첫_단계에서_고정한다(이름: str) -> None:
    """한 워크플로 안의 파이썬들이 같은 DB 를 쓴다 (docs/infra.md 25.472·25.480). 새 워크플로도 여기 걸린다."""
    import re

    import yaml

    단계들 = next(iter(yaml.safe_load((뿌리 / ".github" / "workflows" / 이름).read_text(encoding="utf-8"))
                    ["jobs"].values()))["steps"]  # fmt: skip
    실행들 = [str(s.get("run", "")) for s in 단계들]
    고정 = next(i for i, r in enumerate(실행들) if "batch.core.client --pin" in r and "GITHUB_ENV" in r)
    첫_작업 = next(i for i, r in enumerate(실행들)
                 if re.search(r"python (?:-m batch\.(?!core\.client)|scripts/(?!ops_tee))", r))  # fmt: skip
    assert 고정 < 첫_작업
    # **고정 단계가 DB_BACKEND 를 봐야 한다** (25.481, 교차검증). 단계 env 에만 있으면 고정 단계는 기본값(turso)으로
    # 판정한다
    잡 = next(iter(yaml.safe_load((뿌리 / ".github" / "workflows" / 이름).read_text(encoding="utf-8"))["jobs"].values()))
    보이는_env = {**(잡.get("env") or {}), **(단계들[고정].get("env") or {})}
    assert "DB_BACKEND" in 보이는_env, f"{이름}: 고정 단계에 DB_BACKEND 가 안 보인다"


def test_auto_가_아니면_고정하지_않는다(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """설정값을 못 본 고정 단계가 기본값 turso 로 뒤 단계의 auto 를 덮으면 막힌 Turso 로 간다 (25.481)."""
    import runpy
    import sys

    monkeypatch.delenv("DB_BACKEND", raising=False)
    monkeypatch.setattr(sys, "argv", ["client", "--pin"])
    runpy.run_module("batch.core.client", run_name="__main__")
    assert capsys.readouterr().out == ""
