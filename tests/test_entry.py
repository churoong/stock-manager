"""배치 진입점 공통 처리 (docs/infra.md 25.6). 한도는 고장이 아니라 건너뜀이다."""

from __future__ import annotations

import pathlib

import pytest

from batch.core.entry import guard
from batch.core.turso import TursoError

뿌리 = pathlib.Path(__file__).resolve().parent.parent
JOBS = 뿌리 / "batch" / "jobs"
워크플로들 = 뿌리 / ".github" / "workflows"

#: `guard` 로 감싸지 않아도 되는 파일과 **왜인지** (docs/infra.md 25.188).
#: 예전에는 주석 한 줄이 붙은 집합이었다 — 셋이 왜 빠졌는지 이름만으로는 알 수 없었고,
#: 파일이 사라져도 이름이 남는 것을 아무도 안 봤다
안_감싸는_것 = {
    "__init__.py": "모듈 묶음일 뿐이라 실행되지 않는다",
    "step0_check.py": "연결 자체를 점검하는 작업이다. 한도에 걸린 것도 **보고할 내용**이라"
    " 건너뛰면 안 된다 — 이 작업만은 시끄럽게 실패해야 한다",
    "korfinasc_bench.py": "모델 벤치마크라 DB 를 쓰지 않는다. 한도와 무관하다",
}
NOT_GUARDED = set(안_감싸는_것)


def test_한도면_건너뛰고_0(capsys: pytest.CaptureFixture[str]) -> None:
    def main() -> int:
        raise TursoError("D1 HTTP 400: exceeded D1's free tier daily row write limit")

    assert guard(main) == 0
    assert "건너뜀" in capsys.readouterr().out


def test_다른_오류는_그대로_올린다() -> None:
    def main() -> int:
        raise TursoError("D1 HTTP 401: 인증 실패")

    with pytest.raises(TursoError):
        guard(main)


def test_정상이면_종료_코드를_그대로() -> None:
    assert guard(lambda: 3) == 3


def 감싸야_할_것() -> list[pathlib.Path]:
    return [
        p
        for p in sorted(JOBS.glob("*.py"))
        if p.name not in NOT_GUARDED and "__main__" in p.read_text("utf-8")
    ]


def test_훑기가_실제로_찾아_냈다() -> None:
    """**목록이 비면 아래 검사가 0건으로 통과한다** (docs/infra.md 25.188).

    파라미터가 빈 검사는 pytest 가 아무 말도 안 한다 — 초록으로 지나간다.
    """
    assert len(감싸야_할_것()) >= 25, f"감쌀 작업을 {len(감싸야_할_것())}개밖에 못 찾았다"


def test_안_감싸는_사유_목록이_낡지_않았다() -> None:
    없는것 = sorted(이름 for 이름 in 안_감싸는_것 if not (JOBS / 이름).exists())

    assert not 없는것, f"없는 파일의 사유가 남아 있다: {없는것}"

    짧은것 = [이름 for 이름, 사유 in 안_감싸는_것.items() if len(사유.strip()) < 15]
    assert not 짧은것, f"사유가 너무 짧다: {짧은것}"


@pytest.mark.parametrize("path", 감싸야_할_것(), ids=lambda p: p.name)
def test_예약_작업은_모두_감싼다(path: pathlib.Path) -> None:
    """하나라도 빠지면 한도가 걸린 날 그 작업만 실패 메일을 보낸다."""
    assert "sys.exit(guard(main))" in path.read_text(encoding="utf-8"), f"{path.name} 이 guard 를 쓰지 않는다"


def 워크플로가_부르는_작업() -> set[str]:
    """`.github/workflows` 의 `python -m batch.jobs.X` 에서 X 를 모은다."""
    import re

    나온것: set[str] = set()
    for 길 in sorted(워크플로들.glob("*.yml")):
        나온것 |= set(re.findall(r"-m\s+batch\.jobs\.([A-Za-z0-9_]+)", 길.read_text(encoding="utf-8")))
    return 나온것


def test_워크플로_훑기가_실제로_찾아_냈다() -> None:
    assert len(워크플로가_부르는_작업()) >= 25, sorted(워크플로가_부르는_작업())


@pytest.mark.parametrize("이름", sorted(워크플로가_부르는_작업()))
def test_예약이_부르는_작업은_실제로_돈다(이름: str) -> None:
    """**`__main__` 이 없으면 `python -m` 은 아무것도 안 하고 0 으로 끝난다.**

    워크플로는 초록이고 로그도 깨끗한데 그날 일은 하나도 안 된다 —
    25.0 「잃고서 초록으로 알린다」. 위의 `test_예약_작업은_모두_감싼다` 는
    `"__main__" in …` 으로 **거른 뒤에** 보므로, 진입점을 잃은 작업은
    그 그물에서 **조용히 빠진다.** 그래서 반대 방향으로 한 번 더 본다.
    """
    길 = JOBS / f"{이름}.py"
    assert 길.exists(), f"워크플로가 없는 작업을 부른다: batch.jobs.{이름}"

    글 = 길.read_text(encoding="utf-8")
    assert '__name__ == "__main__"' in 글 or "__name__ == '__main__'" in 글, (
        f"{이름}.py 에 실행 진입점이 없다. `python -m batch.jobs.{이름}` 이"
        " 아무것도 안 하고 0 으로 끝난다 — 워크플로는 초록이다"
    )

    if 길.name in NOT_GUARDED:
        return
    assert "sys.exit(guard(main))" in 글, f"{이름}.py 가 guard 를 쓰지 않는다"


class TestD1_에서_쉬기:
    """docs/infra.md 25.14 — SKIP_ON_D1=1 인 워크플로는 D1 임시 운영 중에 시작하지 않는다."""

    def test_표시가_있고_D1_이면_시작하지_않는다(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from batch.core import client as backend

        monkeypatch.setenv("SKIP_ON_D1", "1")
        monkeypatch.setattr(backend, "resolved_backend", lambda: "d1")
        assert guard(lambda: pytest.fail("돌면 안 된다")) == 0
        assert "쉼" in capsys.readouterr().out

    def test_Turso_로_돌아가면_그대로_돈다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from batch.core import client as backend

        monkeypatch.setenv("SKIP_ON_D1", "1")
        monkeypatch.setattr(backend, "resolved_backend", lambda: "turso")
        assert guard(lambda: 3) == 3

    def test_표시가_없으면_D1_이어도_돈다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 따라잡기·국내 일일 배치는 D1 에서 돌아야 한다. DB 를 볼 필요도 없다
        from batch.core import client as backend

        monkeypatch.delenv("SKIP_ON_D1", raising=False)
        monkeypatch.setattr(backend, "resolved_backend", lambda: pytest.fail("볼 필요가 없다"))
        assert guard(lambda: 3) == 3
