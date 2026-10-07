"""따라잡기 워크플로의 **셸을 실제로 돌려 본다** (docs/infra.md 25.56).

**왜 있나.** 2026-09-21 에 `d1-catchup.yml` 의 단계 블록을 여덟 개 고쳤다(25.52·25.53).
`|| rc=$?` 로 받고 `exit $rc` 로 되돌리는 모양인데, 이것은 **셸의 일**이라 YAML 파싱이나
문자열 검사로는 증명되지 않는다. 그리고 Actions 는 멈춰 있어 돌려 볼 수도 없다.
되살아나는 날 이 셸이 틀리면 **그날 하루를 통째로 버린다.**

그래서 여기서 돌린다. `python` 을 가짜로 바꿔 놓고 진짜 `bash` 로 블록을 실행한다.
확인하는 것:

1. 배치가 죽어도 **측정기가 돈다** — 그 단계의 D1 사용량을 잃지 않는다
2. 죽은 단계는 **죽은 채로 남는다** — `exit $rc` 가 실패를 성공으로 바꾸지 않는다
3. 0번은 파이썬이 터져도 **`use_d1=false` 를 적고 0 으로 끝난다** — 뒤 단계가 조용히
   사라지지 않는다
4. 사용량 줄이 `d1-usage.log` 에도 쌓인다 — 길어서 잘려도 살아남게 (25.55)

**가짜 python 을 쓴다.** 진짜 배치를 돌리면 DB 가 필요하고, 그러면 이 테스트가 오프라인
규칙을 어긴다. 우리가 보려는 것은 배치가 아니라 **그것을 감싼 셸**이다.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML 이 있어야 워크플로를 읽는다")

워크플로 = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "d1-catchup.yml"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash 가 없다")


def 단계(이름앞: str) -> str:
    """워크플로에서 그 단계의 `run` 블록. GitHub 표현식은 빈 값으로 바꾼다."""
    잡 = next(iter(yaml.safe_load(워크플로.read_text(encoding="utf-8"))["jobs"].values()))
    찾음 = [s for s in 잡["steps"] if str(s.get("name", "")).startswith(이름앞)]
    assert len(찾음) == 1, f"{이름앞} 로 시작하는 단계가 {len(찾음)}개다"
    return re.sub(r"\$\{\{[^}]*\}\}", "", str(찾음[0]["run"]))


def 돌리기(블록: str, 작업방: Path, *, 파이썬_종료코드: int = 0, 파이썬_출력: str = "d1") -> subprocess.CompletedProcess:
    """가짜 `python` 을 앞세워 블록을 `bash -e` 로 돌린다 (Actions 의 기본 셸과 같다).

    가짜는 `d1_usage.py` 만은 **언제나 0** 으로 끝낸다 — 진짜도 그렇게 만들어 두었다
    (재는 일이 재어지는 일을 망치면 안 된다).
    """
    가짜 = 작업방 / "bin"
    가짜.mkdir(exist_ok=True)
    (가짜 / "python").write_text(
        "#!/bin/bash\n"
        # ops_tee.py 는 비공개 저장소에서 tee 와 같다 (25.978) — 가짜도 진짜 tee 로 넘긴다
        'if [[ "$1" == *ops_tee.py ]]; then shift; exec tee "$@"; fi\n'
        'if [[ "$*" == *d1_usage.py* ]]; then echo "[D1 사용량] $2"; exit 0; fi\n'
        'if [[ "$*" == *catchup_report.py* ]]; then echo "요약 한 줄"; exit 0; fi\n'
        'if [[ "$*" == *return_check.py* ]]; then echo "[복귀 점검] 판정 한 줄"; exit 0; fi\n'
        f'echo "{파이썬_출력}"\n'
        f"exit {파이썬_종료코드}\n",
        encoding="utf-8",
    )
    (가짜 / "python").chmod(0o755)
    환경 = {
        **os.environ,
        "PATH": f"{가짜}:{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(작업방 / "gh_output"),
    }
    (작업방 / "gh_output").touch()
    return subprocess.run(
        ["bash", "-e", "-c", 블록], cwd=작업방, env=환경, capture_output=True, text=True, timeout=60
    )


class Test단계가_죽어도_사용량은_잰다:
    """25.52 를 셸로 확인한다."""

    def test_배치가_죽으면_단계도_죽는다(self, tmp_path: Path) -> None:
        결과 = 돌리기(단계("5."), tmp_path, 파이썬_종료코드=3)

        assert 결과.returncode == 3, "실패가 성공으로 바뀌었다 — 알림이 '✅ 끝' 을 보낸다"

    def test_배치가_죽어도_측정기는_돌았다(self, tmp_path: Path) -> None:
        돌리기(단계("5."), tmp_path, 파이썬_종료코드=3)

        로그 = (tmp_path / "catchup.log").read_text(encoding="utf-8")
        assert "[D1 사용량]" in 로그, "죽은 단계의 사용량을 잃었다 — 가장 알고 싶은 숫자다"

    def test_잘_끝나면_0_이다(self, tmp_path: Path) -> None:
        결과 = 돌리기(단계("5."), tmp_path)

        assert 결과.returncode == 0
        assert "[D1 사용량]" in (tmp_path / "catchup.log").read_text(encoding="utf-8")

    def test_사용량은_따로도_쌓인다(self, tmp_path: Path) -> None:
        """25.55 — 로그가 길어 잘려도 이 파일은 코멘트 맨 위에 그대로 붙는다."""
        돌리기(단계("5."), tmp_path, 파이썬_종료코드=3)

        assert "[D1 사용량]" in (tmp_path / "d1-usage.log").read_text(encoding="utf-8")

    @pytest.mark.parametrize("이름", ["1.", "2.", "3.", "4.", "5.", "6.", "7.", "8."])
    def test_모든_배치_단계가_같은_모양이다(self, 이름: str, tmp_path: Path) -> None:
        방 = tmp_path / 이름.rstrip(".")
        방.mkdir()

        결과 = 돌리기(단계(이름), 방, 파이썬_종료코드=7)

        assert 결과.returncode == 7, f"{이름} 단계가 실패를 삼킨다"
        assert "[D1 사용량]" in (방 / "catchup.log").read_text(encoding="utf-8"), f"{이름} 단계가 사용량을 잃는다"


class Test0번은_조용히_사라지지_않는다:
    """25.53 을 셸로 확인한다."""

    def test_파이썬이_터져도_값을_적고_0_으로_끝난다(self, tmp_path: Path) -> None:
        결과 = 돌리기(단계("0."), tmp_path, 파이썬_종료코드=1, 파이썬_출력="RuntimeError: D1 토큰 없음")

        assert 결과.returncode == 0, "0번이 죽으면 9·10번까지 건너뛰어진다"
        assert "use_d1=false" in (tmp_path / "gh_output").read_text(encoding="utf-8")

    def test_터진_이유가_로그에_남는다(self, tmp_path: Path) -> None:
        돌리기(단계("0."), tmp_path, 파이썬_종료코드=1, 파이썬_출력="RuntimeError: D1 토큰 없음")

        로그 = (tmp_path / "catchup.log").read_text(encoding="utf-8")
        assert "알아내지 못해" in 로그
        assert "RuntimeError: D1 토큰 없음" in 로그, "이유가 사라지면 빨간 X 만 남는다"

    def test_D1_이면_true_를_적는다(self, tmp_path: Path) -> None:
        돌리기(단계("0."), tmp_path, 파이썬_출력="d1")

        assert "use_d1=true" in (tmp_path / "gh_output").read_text(encoding="utf-8")

    def test_Turso_면_false_를_적는다(self, tmp_path: Path) -> None:
        # 복귀했으면 따라잡을 이유가 없다. 그래도 **값은 적는다**
        돌리기(단계("0."), tmp_path, 파이썬_출력="turso")

        출력 = (tmp_path / "gh_output").read_text(encoding="utf-8")
        assert "use_d1=false" in 출력 and "use_d1=true" not in 출력


class Test결과_알림:
    def test_요약이_로그에도_남는다(self, tmp_path: Path) -> None:
        """25.54 — 텔레그램에만 가면 클라우드 세션이 못 읽는다."""
        돌리기(단계("9."), tmp_path)

        assert "요약 한 줄" in (tmp_path / "catchup.log").read_text(encoding="utf-8")


class Test복귀_점검:
    """9-1 단계를 셸로 확인한다 (docs/infra.md 25.80).

    이 단계는 **되살아나는 날 딱 한 번** 값어치가 있다. 그날 셸이 틀리면 하루를 버린다.
    """

    def test_판정이_두_파일에_다_남는다(self, tmp_path: Path) -> None:
        """`catchup.log` 는 본문, `d1-usage.log` 는 **코멘트 맨 위**다 (publish_output --head-file)."""
        돌리기(단계("9-1."), tmp_path)

        assert "[복귀 점검] 판정 한 줄" in (tmp_path / "catchup.log").read_text(encoding="utf-8")
        assert "[복귀 점검] 판정 한 줄" in (tmp_path / "d1-usage.log").read_text(encoding="utf-8")

    def test_판정이_터져도_단계는_0_으로_끝난다(self, tmp_path: Path) -> None:
        """**확인하는 일이 확인받는 일을 망치면 안 된다.** 여기서 죽으면 10번 기록까지 못 나간다."""
        결과 = 돌리기(단계("9-1."), tmp_path, 파이썬_종료코드=1, 파이썬_출력="터졌다")

        assert 결과.returncode == 0

    def test_앞_단계가_쌓아_둔_사용량을_지우지_않는다(self, tmp_path: Path) -> None:
        """판정을 **읽던 파일에 붙인다.** 덮어쓰면 코멘트 맨 위의 단계별 사용량이 사라진다."""
        (tmp_path / "d1-usage.log").write_text("[D1 사용량] 4. 과거 시세 끝: 오늘 72,000행 썼다\n", encoding="utf-8")

        돌리기(단계("9-1."), tmp_path)

        남은글 = (tmp_path / "d1-usage.log").read_text(encoding="utf-8")
        assert "72,000행" in 남은글, "앞 단계가 잰 숫자를 지웠다"
        assert "[복귀 점검]" in 남은글


class Test할_일이_없던_날은_이슈에_안_올린다:
    """0번과 10번 블록을 **실제로 돌려** 본다 (docs/infra.md 25.84).

    Turso 로 돌아간 뒤에도 이 잡은 날마다 돈다(`DB_BACKEND == 'auto'`). 그때 10번이
    "지금 쓰는 DB: turso" 한 줄짜리 코멘트를 매일 달면 이슈 #1 의 마지막 자리가 덮인다 —
    10월 1일에 읽을 복귀 점검(25.80)이 바로 그 자리다.

    **0번이 깨진 날은 달라야 한다.** 그날이야말로 이유를 읽어야 하므로 코멘트가 나가야 한다(25.53).
    """

    def _내보내기(self) -> str:
        return 단계("10.")

    def test_Turso_면_표식을_남긴다(self, tmp_path: Path) -> None:
        돌리기(단계("0."), tmp_path, 파이썬_출력="turso")

        로그 = (tmp_path / "catchup.log").read_text(encoding="utf-8")
        assert "실행하지 않음: 지금은 turso 를 쓴다" in 로그

    def test_D1_이면_표식을_안_남긴다(self, tmp_path: Path) -> None:
        돌리기(단계("0."), tmp_path, 파이썬_출력="d1")

        assert "실행하지 않음" not in (tmp_path / "catchup.log").read_text(encoding="utf-8")

    def test_0번이_깨진_날에는_표식을_안_남긴다(self, tmp_path: Path) -> None:
        """**입을 막으면 안 되는 날이다.** 어느 DB 인지 알아내지 못한 것이 곧 읽을 이유다."""
        돌리기(단계("0."), tmp_path, 파이썬_종료코드=1, 파이썬_출력="RuntimeError: 토큰 없음")

        로그 = (tmp_path / "catchup.log").read_text(encoding="utf-8")
        assert "실행하지 않음" not in 로그, "0번이 깨진 날까지 코멘트를 막았다"
        assert "RuntimeError: 토큰 없음" in 로그

    def test_표식이_있으면_내보내기가_건너뛴다(self, tmp_path: Path) -> None:
        (tmp_path / "catchup.log").write_text("실행하지 않음: 지금은 turso 를 쓴다\n", encoding="utf-8")

        결과 = 돌리기(self._내보내기(), tmp_path)

        assert 결과.returncode == 0
        assert "이슈에 올리지 않는다" in 결과.stdout
        assert "publish_output" not in 결과.stdout.replace("올리지 않는다", "")

    def test_표식이_없으면_내보낸다(self, tmp_path: Path) -> None:
        (tmp_path / "catchup.log").write_text("[0단계] 지금 쓰는 DB: d1\n유니버스 879종목\n", encoding="utf-8")

        결과 = 돌리기(self._내보내기(), tmp_path)

        assert 결과.returncode == 0
        assert "이슈에 올리지 않는다" not in 결과.stdout

    def test_로그가_아예_없어도_죽지_않는다(self, tmp_path: Path) -> None:
        """한 단계도 못 돌고 멈춘 날. `grep` 이 없는 파일에 1 을 돌려주는데 `bash -e` 가 물면 안 된다."""
        결과 = 돌리기(self._내보내기(), tmp_path)

        assert 결과.returncode == 0, 결과.stderr


class Test읽기_문이_미루면_그_단계를_건너뛴다:
    """25.844·25.847 의 문을 셸로 확인한다 — 문자열 검사로는 `|| gate=$?` 와 `tee` 파이프의 종료 코드를 증명하지 못한다."""

    @staticmethod
    def _돌리기(블록: str, 작업방: Path, 문_종료코드: int) -> subprocess.CompletedProcess:
        가짜 = 작업방 / "bin"
        가짜.mkdir(exist_ok=True)
        (가짜 / "python").write_text(
            "#!/bin/bash\n"
            'if [[ "$1" == *ops_tee.py ]]; then shift; exec tee "$@"; fi\n'
            'if [[ "$*" == *d1_read_gate.py* ]]; then\n'
            f'  if [ {문_종료코드} = 78 ]; then echo "deferred=true" >> "$GITHUB_OUTPUT"; fi\n'
            f'  echo "[읽기 문] $2"; exit {문_종료코드}\n'
            "fi\n"
            'if [[ "$*" == *d1_usage.py* ]]; then echo "[D1 사용량] $2"; exit 0; fi\n'
            'echo "배치 돌았다" >> ran.txt\n'
            "exit 0\n",
            encoding="utf-8",
        )
        (가짜 / "python").chmod(0o755)
        (작업방 / "gh_output").touch()
        환경 = {**os.environ, "PATH": f"{가짜}:{os.environ['PATH']}", "GITHUB_OUTPUT": str(작업방 / "gh_output")}
        return subprocess.run(["bash", "-e", "-c", 블록], cwd=작업방, env=환경, capture_output=True, text=True, timeout=60)

    @pytest.mark.parametrize("이름", ["4.", "5.", "6.", "7.", "8."])
    def test_78_이면_배치를_돌리지_않고_0_으로_끝나며_미룸을_남긴다(self, 이름: str, tmp_path: Path) -> None:
        결과 = self._돌리기(단계(이름), tmp_path, 78)
        assert 결과.returncode == 0, 결과.stderr
        assert not (tmp_path / "ran.txt").exists(), f"{이름} 미룬 단계의 배치가 돌았다"
        assert "deferred=true" in (tmp_path / "gh_output").read_text(encoding="utf-8")
        assert "[읽기 문]" in (tmp_path / "catchup.log").read_text(encoding="utf-8")

    @pytest.mark.parametrize("이름", ["4.", "6."])
    def test_문이_다른_까닭으로_실패하면_예전처럼_들어간다(self, 이름: str, tmp_path: Path) -> None:
        결과 = self._돌리기(단계(이름), tmp_path, 1)
        assert 결과.returncode == 0, 결과.stderr
        assert (tmp_path / "ran.txt").exists(), "재는 일이 실패했다고 단계를 건너뛰었다"
