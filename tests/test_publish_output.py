"""Actions 출력을 이슈 코멘트로 내보내기 (scripts/publish_output.py).

왜 이 통로가 있는지는 docs/infra.md 25.17. 요지는 **클라우드 세션이 Actions 로그를 못 읽는다**는
것이고, 그래서 결과를 이슈로 한 부 더 낸다.

여기서 지키는 것은 세 가지다.
1. 자르기가 상한을 넘지 않고, **앞과 끝을 모두 남긴다** (무엇을 돌렸나는 앞, 오류는 끝).
2. 이슈를 고를 때 **풀 리퀘스트를 이슈로 착각하지 않는다** (목록 API 가 섞어서 준다).
3. 내보내기가 실패해도 **배치를 죽이지 않는다** — 종료코드 0.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import publish_output as po  # noqa: E402


class Test자르기:
    def test_짧으면_그대로다(self) -> None:
        assert po.truncate("가나다", 100) == "가나다"

    def test_상한을_넘지_않는다(self) -> None:
        assert len(po.truncate("가" * 5_000, 1_000)) <= 1_000

    def test_앞과_끝이_모두_남는다(self) -> None:
        """오류는 끝에 있다. 앞만 남기면 정작 필요한 것이 잘린다."""
        text = "머리" + "중" * 5_000 + "꼬리"
        cut = po.truncate(text, 1_000)
        assert cut.startswith("머리")
        assert cut.endswith("꼬리")

    def test_덜어_냈다고_말한다(self) -> None:
        assert "덜어 냄" in po.truncate("가" * 5_000, 1_000)


class Test코멘트본문:
    def test_본문을_펜스로_감싼다(self) -> None:
        body = po.comment_body("결과", "DB 상태", None)
        assert "~~~\n결과\n~~~" in body

    def test_본문에_코드펜스가_있어도_깨지지_않는다(self) -> None:
        """SQL 결과에 ``` 가 섞여 들어와도 물결표 펜스는 닫힌다."""
        body = po.comment_body("```python\nx\n```", "DB 상태", None)
        assert body.count("~~~") == 2

    def test_실행_기록_링크를_넣는다(self) -> None:
        assert "[실행 기록](https://x/run/1)" in po.comment_body("결과", "메모", "https://x/run/1")

    def test_머리말에_무엇인지_적는다(self) -> None:
        assert "**DB 상태**" in po.comment_body("결과", "DB 상태", None)


class Test이슈고르기:
    @staticmethod
    def _가짜(목록: list[dict]):
        def request(url: str, token: str, method: str = "GET", payload: dict | None = None) -> object:
            return 목록

        return request

    def test_제목이_같은_열린_이슈를_찾는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(po, "_request", self._가짜([{"number": 7, "title": "운영 출력"}]))
        assert po.find_issue("o/r", "t", "운영 출력") == 7

    def test_풀_리퀘스트는_이슈가_아니다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """이슈 목록 API 는 PR 도 섞어서 준다. 제목이 같다고 PR 에 코멘트를 달면 안 된다."""
        monkeypatch.setattr(
            po, "_request", self._가짜([{"number": 7, "title": "운영 출력", "pull_request": {"url": "..."}}])
        )
        assert po.find_issue("o/r", "t", "운영 출력") is None

    def test_없으면_None(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(po, "_request", self._가짜([{"number": 7, "title": "다른 것"}]))
        assert po.find_issue("o/r", "t", "운영 출력") is None


class Test배치를_죽이지_않는다:
    def test_토큰이_없으면_건너뛰고_0(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        monkeypatch.setattr(sys, "argv", ["publish_output.py", "--title", "운영 출력"])
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("결과"))
        assert po.main() == 0
        assert "건너뜀" in capsys.readouterr().out

    def test_API_가_터져도_0(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "t")
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
        monkeypatch.delenv("GITHUB_RUN_ID", raising=False)

        def 터진다(*args: object, **kwargs: object) -> object:
            raise RuntimeError("연결 실패")

        monkeypatch.setattr(po, "publish", 터진다)
        monkeypatch.setattr(sys, "argv", ["publish_output.py", "--title", "운영 출력"])
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("결과"))
        assert po.main() == 0
        assert "내보내기 실패" in capsys.readouterr().out


class Test내보낼_것이_없을_때:
    """따라잡기가 한 줄도 못 남기고 멈출 수 있다(0번 단계에서 끝나는 등).
    그때 내보내기 단계가 실패하면 **멀쩡한 실행이 실패로 보인다.**"""

    def test_파일이_없으면_조용히_0(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "t")
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
        monkeypatch.setattr(sys, "argv", ["p.py", "--title", "운영 출력", "--file", str(tmp_path / "없다.log")])
        assert po.main() == 0
        assert "내보낼 것이 없다" in capsys.readouterr().out

    def test_내용이_비었으면_이슈를_더럽히지_않는다(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, tmp_path: Path
    ) -> None:
        빈파일 = tmp_path / "빈.log"
        빈파일.write_text("   \n", encoding="utf-8")
        monkeypatch.setenv("GITHUB_TOKEN", "t")
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")

        def 불리면안된다(*args: object, **kwargs: object) -> object:
            raise AssertionError("빈 내용으로 코멘트를 달면 안 된다")

        monkeypatch.setattr(po, "publish", 불리면안된다)
        monkeypatch.setattr(sys, "argv", ["p.py", "--title", "운영 출력", "--file", str(빈파일)])
        assert po.main() == 0
        assert "내보낼 것이 없다" in capsys.readouterr().out


class Test잘리면_안_되는_앞부분:
    """`--head-file` 로 붙인 줄은 **절대 잘리지 않는다** (docs/infra.md 25.55).

    따라잡기 로그는 6만 자를 넘길 수 있다. 그러면 `truncate()` 가 **가운데를 덜어 내는데**,
    하필 가운데에 있는 것이 `4. 과거 시세 끝` 같은 단계별 D1 사용량이다. 하루 한 번뿐인
    실행에서 가장 알고 싶은 숫자가 길이 때문에 사라지면 그날은 헛수고다.
    """

    사용량 = "[D1 사용량] 4. 과거 시세 끝: 오늘 71,234행 썼다 (71%)"

    def test_본문이_길어도_앞부분은_남는다(self) -> None:
        긴본문 = "x" * 500_000

        몸 = po.comment_body(긴본문, "따라잡기", None, self.사용량)

        assert self.사용량 in 몸
        assert "덜어 냄" in 몸, "본문은 잘려야 한다 (앞부분만 남기고 끝나면 안 된다)"

    def test_앞부분이_없으면_예전과_같다(self) -> None:
        몸 = po.comment_body("한 줄", "따라잡기", None)

        assert "한 줄" in 몸
        assert 몸.count("~~~") == 2

    def test_전체_길이가_코멘트_한도_안이다(self) -> None:
        """앞부분을 붙인 만큼 본문 몫을 줄이지 않으면 **코멘트가 통째로 거부된다.**

        그러면 잘린 기록조차 남지 않는다 — 없느니만 못하다.
        """
        몸 = po.comment_body("x" * 500_000, "따라잡기", None, "usage\n" * 2_000)

        assert len(몸) <= 65_000, f"{len(몸):,}자 — GitHub 코멘트 한도를 넘는다"

    def test_앞부분만_있고_본문이_짧으면_둘_다_남는다(self) -> None:
        몸 = po.comment_body("짧은 본문", "따라잡기", None, self.사용량)

        assert self.사용량 in 몸 and "짧은 본문" in 몸
        assert "덜어 냄" not in 몸
