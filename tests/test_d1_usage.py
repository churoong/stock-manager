"""D1 사용량 한 줄 찍기 (scripts/d1_usage.py, docs/infra.md 25.26).

따라잡기의 여유(`--budget-reserve 32000`)는 **근거 없이 잡힌 숫자**다. 뒤 단계와 아침 배치가
얼마를 쓰는지 아무도 재 본 적이 없다. 단계마다 이 한 줄을 남겨 **어림을 실측으로 바꾼다.**

재는 일이 재어지는 일을 망치면 안 된다 — 실패해도 종료코드 0 이다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import d1_usage as du  # noqa: E402


class Test한줄:
    def test_쓴_행과_비율을_함께_적는다(self) -> None:
        줄 = du.한줄("4. 과거 시세 끝", 쓴=75_000, 한도=100_000)
        assert "75,000행" in 줄 and "(75%)" in 줄 and "25,000행" in 줄

    def test_라벨이_들어간다(self) -> None:
        """단계 이름이 없으면 뺄셈을 할 수 없다. 그게 이 줄의 목적이다."""
        assert "2. 재무 끝" in du.한줄("2. 재무 끝", 쓴=1, 한도=100_000)

    def test_다_썼으면_100퍼센트(self) -> None:
        assert "(100%)" in du.한줄("끝", 쓴=100_000, 한도=100_000)

    def test_한_줄도_안_썼으면_0퍼센트(self) -> None:
        assert "(0%)" in du.한줄("시작", 쓴=0, 한도=100_000)

    def test_한도가_0_이어도_나누지_않는다(self) -> None:
        """0 으로 나누면 재는 일이 배치를 죽인다."""
        assert "(0%)" in du.한줄("시작", 쓴=0, 한도=0)

    def test_한도보다_많이_썼어도_남은_것이_음수가_안_된다(self) -> None:
        assert "남은 0행" in du.한줄("넘김", 쓴=262_045, 한도=100_000)

    def test_못_읽었으면_0_이_아니라_모름이다(self) -> None:
        """**0 으로 적으면 "아직 아무것도 안 썼다" 로 읽힌다.** 그 한 줄을 보고 더 돌리게 된다 —
        2026-09-20 에 이 자리가 실제로 0 을 내놓고 있었다."""
        줄 = du.한줄("시작", 쓴=None, 한도=100_000)
        assert "읽지 못했다" in 줄
        assert "0행 썼다" not in 줄


def test_DB_가_없어도_0_으로_끝난다(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """운영에서는 .env 없이 돌 수 있다. 재지 못해도 따라잡기를 멈추면 안 된다."""
    def 터진다(*args: object, **kwargs: object) -> object:
        raise RuntimeError("TURSO_DATABASE_URL 이 비어 있습니다")

    monkeypatch.setattr(du, "TursoClient", 터진다)
    monkeypatch.setattr(sys, "argv", ["d1_usage.py", "시작"])
    assert du.main() == 0
    assert "재지 못했다" in capsys.readouterr().out


class Test쓰기와_읽기를_구별한다:
    """**머리표가 갈라져 있어야 한다** (2026-09-22, docs/infra.md 25.122).

    읽기 줄을 더하면서 같은 머리표(`[D1 사용량]`)를 쓰면, `return_check.여유가_맞았나` 가
    마지막 줄을 "그때까지 쓴 행" 으로 읽는다 — 그 줄이 **훑은 행(수백만)** 이면 판정이
    "여유를 수백만으로 늘려라" 가 된다. 두 스크립트를 잇는 글자는 한 곳에서 온다(25.113).
    """

    def test_읽기_줄은_다른_머리표를_쓴다(self) -> None:
        assert du.READ_MARK != du.WRITE_MARK

    def test_되살아나는_날_점검이_읽기_줄을_세지_않는다(self) -> None:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import return_check as rc

        글 = "\n".join([
            du.한줄("4. 과거 시세 끝", 쓴=72_340, 한도=100_000),
            du.한줄("4. 과거 시세 끝", 쓴=3_100_000, 한도=5_000_000, 무엇="훑었다", mark=du.READ_MARK),
            du.한줄("8. 신호 끝", 쓴=91_000, 한도=100_000),
            du.한줄("8. 신호 끝", 쓴=4_800_000, 한도=5_000_000, 무엇="훑었다", mark=du.READ_MARK),
        ])

        사용량 = rc.사용량_읽기(글)

        assert [수 for _, 수 in 사용량] == [72_340, 91_000], "훑은 행이 쓴 행으로 섞여 들어왔다"
        판정 = rc.여유가_맞았나(사용량)
        assert 판정.상태 == "ok", f"5~8단계 몫은 18,660행인데 {판정.내용}"


class Test용량은_행이_아니다:
    """**같은 수를 한쪽은 MB, 한쪽은 "행" 으로 적었다** (docs/infra.md 25.151).

    쓰기·읽기는 **오늘 센 행**이고 용량은 **지금 차 있는 바이트**다. 셋을 한 문장 틀로
    찍으면서 단위와 기간을 안 갈랐더니 용량 줄이 이렇게 나왔다.

        [D1 용량] 시작: 오늘 314,572,800행 찼다 (60%), 남은 …행

    되살아나는 날 이 줄을 읽는 사람은 "3억 행을 썼다" 로 읽는다. 웹의 `/status` 게이지는
    같은 값을 이미 MB 로 보여 준다(`web/lib/limits.usageAmount`) — 한 수에 두 단위였다.
    """

    def test_용량은_MB_로_찍는다(self) -> None:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from d1_usage import SIZE_MARK, 메가바이트로, 한줄

        줄 = 한줄("시작", 314_572_800, 500 * 1024 * 1024, "찼다", SIZE_MARK, 메가바이트로, "")

        assert "300MB" in 줄
        assert "행" not in 줄, "바이트를 행이라고 적었다"
        assert "오늘" not in 줄, "리셋이 없는 수위를 '오늘' 이라고 적었다"
        assert "60%" in 줄

    def test_쓰기_읽기는_그대로_행이다(self) -> None:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from d1_usage import READ_MARK, 한줄

        assert "오늘 72,340행 썼다" in 한줄("4. 과거 시세 끝", 72_340, 100_000)
        assert "오늘 4,200,000행 훑었다" in 한줄("x", 4_200_000, 5_000_000, "훑었다", READ_MARK)

    def test_못_읽어도_단위는_맞는다(self) -> None:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from d1_usage import SIZE_MARK, 메가바이트로, 한줄

        줄 = 한줄("시작", None, 500 * 1024 * 1024, "찼다", SIZE_MARK, 메가바이트로, "")

        assert "읽지 못했다" in 줄
        assert "한도 500MB" in 줄, "못 읽은 줄에서만 단위가 어긋나면 그날 그것만 이상해 보인다"

    def test_웹과_같은_단위로_보여_준다(self) -> None:
        """한 수에 두 단위를 두지 않는다 (25.0 「한 규칙이 두 곳에 있다」)."""
        from pathlib import Path

        글 = (Path(__file__).resolve().parent.parent / "web" / "lib" / "limits.ts").read_text(
            encoding="utf-8"
        )
        assert "1_048_576" in 글 and "MB" in 글, "웹이 MB 로 안 보여 주면 이 검사의 전제가 바뀐다"

    def test_실제로_찍는_줄이_MB_다(self, monkeypatch, capsys) -> None:
        """**부르는 자리까지 본다.** 위 검사들은 `한줄` 을 직접 불러서 본다 —

        `main()` 이 단위를 안 넘기도록 되돌려도 하나도 안 깨졌다(2026-09-23 실제로 그랬다).
        25.0 「테스트가 진짜 길로 안 지난다」. 여기서는 가짜 클라이언트를 깔고 `main()` 을 돌린다.
        """
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import d1_usage as du

        from batch.core import db as core_db

        class 가짜:
            def close(self) -> None:
                pass

        monkeypatch.setattr(du, "TursoClient", lambda *a, **k: 가짜())
        monkeypatch.setattr(core_db, "d1_writes_today", lambda c: 1_000)
        monkeypatch.setattr(core_db, "d1_reads_today", lambda c: 2_000)
        monkeypatch.setattr(du, "적힌_용량", lambda c: 314_572_800)
        monkeypatch.setattr(sys, "argv", ["d1_usage.py", "시작"])

        assert du.main() == 0
        줄들 = capsys.readouterr().out.splitlines()

        용량줄 = next(줄 for 줄 in 줄들 if 줄.startswith(du.SIZE_MARK))
        assert "300MB" in 용량줄, f"용량을 MB 로 안 찍었다: {용량줄}"
        assert "행" not in 용량줄, f"바이트를 행이라 찍었다: {용량줄}"

        쓰기줄 = next(줄 for 줄 in 줄들 if 줄.startswith(du.WRITE_MARK))
        assert "1,000행" in 쓰기줄, "쓰기는 그대로 행이어야 한다"
