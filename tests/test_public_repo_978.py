"""공개 저장소로 옮길 준비 (docs/public-repo.md, docs/infra.md 25.978) — 운영 출력이 남이 보는 로그·이슈에 남지 않게."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ops_visibility  # noqa: E402
import publish_output  # noqa: E402


def _event(tmp_path: Path, repo: dict) -> dict[str, str]:
    p = tmp_path / "event.json"
    p.write_text(json.dumps({"repository": repo}), encoding="utf-8")
    return {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_PATH": str(p)}


class Test공개_판정:
    def test_이벤트의_visibility(self, tmp_path: Path) -> None:
        assert ops_visibility.is_public(_event(tmp_path, {"visibility": "public", "private": False}))
        assert not ops_visibility.is_public(_event(tmp_path, {"visibility": "private", "private": True}))
        assert not ops_visibility.is_public(_event(tmp_path, {"private": True}))

    def test_모르면_공개로_본다(self, tmp_path: Path) -> None:
        assert ops_visibility.is_public(_event(tmp_path, {}))
        assert ops_visibility.is_public({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_PATH": str(tmp_path / "없음")})

    def test_Actions_밖은_비공개_강제값이_먼저(self) -> None:
        assert not ops_visibility.is_public({})
        assert ops_visibility.is_public({"REPO_PUBLIC": "true"})
        assert not ops_visibility.is_public({"REPO_PUBLIC": "false", "GITHUB_ACTIONS": "true"})


class Test로그_대신_파일:
    def _run(self, tmp_path: Path, public: str) -> subprocess.CompletedProcess:
        env = {"PATH": "/usr/bin:/bin", "REPO_PUBLIC": public}
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "ops_tee.py"), "-a", "a.log", "b.log"],
            input="보유 삼성전자 1,000주\n평가 11억원\n", cwd=tmp_path, env=env, capture_output=True, text=True,
            timeout=30,
        )

    def test_공개면_로그에_찍지_않고_파일에만(self, tmp_path: Path) -> None:
        r = self._run(tmp_path, "true")
        assert r.returncode == 0 and "삼성전자" not in r.stdout and "2줄" in r.stdout
        assert (tmp_path / "a.log").read_text(encoding="utf-8") == "보유 삼성전자 1,000주\n평가 11억원\n"
        assert (tmp_path / "b.log").read_text(encoding="utf-8").count("\n") == 2

    def test_비공개면_tee_와_같다(self, tmp_path: Path) -> None:
        r = self._run(tmp_path, "false")
        assert r.stdout == "보유 삼성전자 1,000주\n평가 11억원\n"


class Test이슈_대상:
    def test_운영_저장소가_있으면_그리로(self) -> None:
        env = {"OPS_REPO": "me/ops", "OPS_TOKEN": "t", "REPO_PUBLIC": "true", "GITHUB_REPOSITORY": "me/app"}
        assert publish_output.target(env) == ("me/ops", "t", None)

    def test_공개인데_운영_저장소가_없으면_올리지_않는다(self) -> None:
        repo, token, why = publish_output.target({"REPO_PUBLIC": "true", "GITHUB_REPOSITORY": "me/app",
                                                   "GITHUB_TOKEN": "t"})  # fmt: skip
        assert (repo, token) == ("", "") and "공개 저장소" in why

    def test_비공개면_예전처럼_이_저장소(self) -> None:
        env = {"REPO_PUBLIC": "false", "GITHUB_REPOSITORY": "me/app", "GITHUB_TOKEN": "t", "OPS_REPO": ""}
        assert publish_output.target(env) == ("me/app", "t", None)


class Test배치_로그:
    """25.979 — 포트폴리오·매도 플래그가 공개 저장소에서는 금액·종목 줄을 로그에 찍지 않는다."""

    def test_포트폴리오_평가액은_공개면_찍지_않는다(self) -> None:
        src = (ROOT / "batch" / "jobs" / "portfolio.py").read_text(encoding="utf-8")
        i = src.index("if visibility.is_public():")
        assert src.index("평가액 {totals") > i, "평가액 줄이 공개 판정보다 먼저 찍힌다"

    def test_매도_플래그_근거는_공개면_찍지_않는다(self) -> None:
        src = (ROOT / "batch" / "jobs" / "sell_flags.py").read_text(encoding="utf-8")
        i = src.index("if visibility.is_public():")
        assert src.index('print(f"  [{f.level}] {f.rationale_text}")') > i
