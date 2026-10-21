"""공개 Actions 로그로 보유·관심 종목·웹앱 주소가 새지 않게 (docs/public-repo.md, docs/infra.md 25.1638)."""

from __future__ import annotations

from pathlib import Path

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def test_미국_공시_수집은_운영_출력으로만() -> None:
    """수집 대상이 보유·관심 종목이라 "SEC 티커 목록에 없는 심볼"·실패 CIK 줄이 그것을 드러냈다."""
    글 = (WF / "disclosures-us.yml").read_text(encoding="utf-8")
    줄 = [x for x in 글.splitlines() if "batch.jobs.disclosures_us" in x and not x.strip().startswith("#")]
    assert 줄 and all("| python scripts/ops_tee.py" in x for x in 줄), 줄
    assert "set -o pipefail" in 글 and "scripts/publish_output.py" in 글 and "if: always()" in 글


def test_웹앱_주소는_시크릿을_먼저() -> None:
    """Actions 변수는 단계 머리 `env:` 에 평문으로 찍힌다 — 시크릿이면 가려진다."""
    for 이름 in ("daily-kr.yml", "daily-us.yml"):
        글 = (WF / 이름).read_text(encoding="utf-8")
        assert "APP_URL: ${{ secrets.APP_URL || vars.APP_URL }}" in 글, 이름
        assert "APP_URL: ${{ vars.APP_URL }}" not in 글, 이름


def test_운영_출력은_잘못된_바이트에도_죽지_않는다(tmp_path: Path) -> None:
    """감사 [권고] — 죽으면 파일엔 앞부분만 남고 앞쪽 배치가 BrokenPipe 로 멈출 수 있었다."""
    import os
    import subprocess
    import sys

    뿌리 = Path(__file__).resolve().parent.parent
    로그 = tmp_path / "run.log"
    r = subprocess.run([sys.executable, str(뿌리 / "scripts" / "ops_tee.py"), str(로그)], input=b"ok\n\xff\xfe bad\nend\n",
                       capture_output=True, env={**os.environ, "REPO_PUBLIC": "true", "PYTHONIOENCODING": "utf-8:strict"}, check=False)  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert 로그.read_text(encoding="utf-8").splitlines()[-1] == "end"


def test_운영_출력으로_넘기는_파이썬은_stderr_까지() -> None:
    """교차검증 [권고] — `2>&1` 이 빠지면 stderr 로 나가는 경고·예외(보유 CIK·금액이 든 줄)가 공개 로그에 찍힌다."""
    빠짐 = []
    for 파일 in sorted(WF.glob("*.yml")):
        글 = 파일.read_text(encoding="utf-8").replace("\\\n", " ")  # 줄 이음을 한 줄로
        for 줄 in 글.splitlines():
            s = 줄.strip()
            if s.startswith("#") or "| python scripts/ops_tee.py" not in s:
                continue
            앞 = s.split("| python scripts/ops_tee.py", 1)[0]
            if "python " in 앞 and "2>&1" not in 앞:
                빠짐.append(f"{파일.name}: {s[:120]}")
    assert not 빠짐, 빠짐
