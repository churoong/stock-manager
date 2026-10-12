"""공개 저장소 아티팩트에 평문 백업(매매·배당·설정)이 올라가던 것 (docs/infra.md 25.1125)."""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backup_crypt.sh"
WF = ROOT / ".github" / "workflows"

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl 없음")


def _run(mode: str, d: Path, **env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k not in ("BACKUP_KEY", "TURSO_AUTH_TOKEN", "GITHUB_ACTIONS")}
    return subprocess.run(["bash", str(SCRIPT), mode, str(d)], env={**base, **env}, capture_output=True, text=True)


def _백업(d: Path) -> bytes:
    본문 = b"INSERT INTO trades VALUES (1, 'buy', 123.45);\n"
    with gzip.open(d / "stock-manager-essential-2026-10-11.sql.gz", "wb") as f:
        f.write(본문)
    return 본문


def test_암호화하면_평문이_남지_않고_같은_키로_되돌린다(tmp_path: Path) -> None:
    본문 = _백업(tmp_path)
    r = _run("encrypt", tmp_path, TURSO_AUTH_TOKEN="tok-123")
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stock-manager-essential-2026-10-11.sql.gz.enc"]
    enc = (tmp_path / "stock-manager-essential-2026-10-11.sql.gz.enc").read_bytes()
    assert b"trades" not in enc and "tok-123" not in r.stdout + r.stderr
    r = _run("decrypt", tmp_path, TURSO_AUTH_TOKEN="tok-123")
    assert r.returncode == 0, r.stderr
    with gzip.open(tmp_path / "stock-manager-essential-2026-10-11.sql.gz", "rb") as f:
        assert f.read() == 본문


def test_BACKUP_KEY_가_있으면_그것을_쓰고_다른_키로는_열리지_않는다(tmp_path: Path) -> None:
    _백업(tmp_path)
    assert _run("encrypt", tmp_path, BACKUP_KEY="k1", TURSO_AUTH_TOKEN="tok").returncode == 0
    틀림 = _run("decrypt", tmp_path, TURSO_AUTH_TOKEN="tok")  # BACKUP_KEY 없이 토큰 키로는 못 연다
    assert 틀림.returncode != 0 and "복호화 실패" in 틀림.stderr
    assert not (tmp_path / "stock-manager-essential-2026-10-11.sql.gz").exists()
    assert _run("decrypt", tmp_path, BACKUP_KEY="k1").returncode == 0


def test_키가_없으면_멈추고_평문을_지우지_않는다(tmp_path: Path) -> None:
    _백업(tmp_path)
    r = _run("encrypt", tmp_path)
    assert r.returncode != 0 and "평문으로 올리지 않고" in r.stderr
    assert not list(tmp_path.glob("*.enc"))


def test_옛_평문_백업은_복호화가_건드리지_않는다(tmp_path: Path) -> None:
    _백업(tmp_path)
    assert _run("decrypt", tmp_path, TURSO_AUTH_TOKEN="t").returncode == 0
    assert (tmp_path / "stock-manager-essential-2026-10-11.sql.gz").exists()


def test_워크플로는_암호화한_것만_올리고_되살리기_전에_푼다() -> None:
    backup = (WF / "backup.yml").read_text(encoding="utf-8")
    assert "bash scripts/backup_crypt.sh encrypt backup" in backup
    assert "path: backup/*.sql.gz.enc" in backup and "path: backup/*.sql.gz\n" not in backup
    assert backup.index("backup_crypt.sh encrypt") < backup.index("upload-artifact")
    for name in ("restore-backup.yml", "restore-drill.yml"):
        src = (WF / name).read_text(encoding="utf-8")
        assert src.index("backup_crypt.sh decrypt backup") < src.index("ls backup/*.sql.gz | head -1"), name
        assert "BACKUP_KEY: ${{ secrets.BACKUP_KEY }}" in src
    # 이 저장소에서 아티팩트를 올리는 곳은 암호화한 백업 하나뿐이다
    올림 = [p.name for p in WF.glob("*.yml") if "upload-artifact" in p.read_text(encoding="utf-8")]
    assert 올림 == ["backup.yml"]


def test_BACKUP_KEY_를_넣어도_그_전_토큰_키_백업이_열린다(tmp_path: Path) -> None:
    """풀기가 첫 키 하나만 써 보아 `BACKUP_KEY` 를 넣는 순간 그 전 백업이 안 열렸다 (docs/infra.md 25.1136, 백업 감사 재현)."""
    본문 = _백업(tmp_path)
    assert _run("encrypt", tmp_path, TURSO_AUTH_TOKEN="tok").returncode == 0  # 예전: 토큰 키로 잠금
    r = _run("decrypt", tmp_path, BACKUP_KEY="새키", TURSO_AUTH_TOKEN="tok")  # 지금: BACKUP_KEY 도 있다
    assert r.returncode == 0, r.stderr
    assert "TURSO_AUTH_TOKEN 에서 끌어낸 키" in r.stdout
    with gzip.open(tmp_path / "stock-manager-essential-2026-10-11.sql.gz", "rb") as f:
        assert f.read() == 본문
