"""시세 사본은 암호화본만 캐시에 넣는다 — 공개 저장소의 Actions 캐시에 KRX 시세를 평문으로 두지 않는다 (docs/infra.md 25.1249)."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ["backtest", "daily-kr", "daily-us", "metrics", "signal-outcomes", "valuation-bands"]
needs = pytest.mark.skipif(not shutil.which("openssl"), reason="openssl 없음")


def _steps(name: str) -> list[dict]:
    wf = yaml.safe_load((ROOT / ".github/workflows" / f"{name}.yml").read_text(encoding="utf-8"))
    return [s for job in wf["jobs"].values() for s in job.get("steps", [])]


@pytest.mark.parametrize("name", WORKFLOWS)
def test_캐시에_넣는_것은_암호화본뿐(name: str) -> None:
    steps = _steps(name)
    넣기 = [s for s in steps if s.get("uses", "").startswith("actions/cache/save")]
    assert 넣기 and all(s["with"]["path"] == ".price-replica-enc" for s in 넣기)
    assert all("replica_crypt.outputs.ready == 'true'" in s["if"] for s in 넣기)
    # 평문 경로를 꺼내는 것은 암호화본이 없을 때 한 번뿐(옮겨 담기)
    for s in steps:
        if s.get("uses", "").startswith("actions/cache/restore") and s["with"]["path"] == ".price-replica":
            assert "cache-matched-key == ''" in s.get("if", "")


def _돌림(mode: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(ROOT / "scripts/replica_crypt.sh"), mode], cwd=cwd, env=env,
                          capture_output=True, text=True)  # fmt: skip


@needs
def test_암호화와_풀기가_왕복한다(tmp_path: Path) -> None:
    (tmp_path / ".price-replica").mkdir()
    db = tmp_path / ".price-replica/prices.db"
    sqlite3.connect(db).executescript("CREATE TABLE prices (x); INSERT INTO prices VALUES ('005930,70000');")
    env = {**os.environ, "TURSO_AUTH_TOKEN": "tok", "GITHUB_OUTPUT": str(tmp_path / "out")}
    env.pop("BACKUP_KEY", None)
    env.pop("GITHUB_ACTIONS", None)
    assert _돌림("encrypt", tmp_path, env).returncode == 0
    enc = (tmp_path / ".price-replica-enc/prices.db.enc").read_bytes()
    assert b"005930" not in enc and "ready=true" in (tmp_path / "out").read_text()
    db.unlink()
    assert _돌림("decrypt", tmp_path, env).returncode == 0
    assert sqlite3.connect(db).execute("SELECT x FROM prices").fetchone()[0] == "005930,70000"


@needs
def test_키가_없으면_넣지_않는다(tmp_path: Path) -> None:
    (tmp_path / ".price-replica").mkdir()
    sqlite3.connect(tmp_path / ".price-replica/prices.db").execute("CREATE TABLE t (x)")
    env = {k: v for k, v in os.environ.items() if k not in ("BACKUP_KEY", "TURSO_AUTH_TOKEN", "GITHUB_ACTIONS")}
    env["GITHUB_OUTPUT"] = str(tmp_path / "out")
    assert _돌림("encrypt", tmp_path, env).returncode == 0
    assert "ready=false" in (tmp_path / "out").read_text() and not (tmp_path / ".price-replica-enc").exists()


@needs
def test_틀린_키면_사본_없이_간다(tmp_path: Path) -> None:
    (tmp_path / ".price-replica").mkdir()
    sqlite3.connect(tmp_path / ".price-replica/prices.db").execute("CREATE TABLE t (x)")
    env = {**os.environ, "TURSO_AUTH_TOKEN": "a", "GITHUB_OUTPUT": str(tmp_path / "out")}
    env.pop("BACKUP_KEY", None)
    env.pop("GITHUB_ACTIONS", None)
    _돌림("encrypt", tmp_path, env)
    (tmp_path / ".price-replica/prices.db").unlink()
    r = _돌림("decrypt", tmp_path, {**env, "TURSO_AUTH_TOKEN": "b"})
    assert r.returncode == 0 and not (tmp_path / ".price-replica/prices.db").exists()
