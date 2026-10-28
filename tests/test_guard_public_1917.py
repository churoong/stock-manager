"""25.1917 — 공개 저장소에서 잡히지 않은 예외의 본문을 Actions 로그에 찍지 않는다 (25.1868 교차검증 [참고]).

`entry.guard` 는 DB 한도가 아닌 예외를 다시 던졌다 — traceback 의 메시지(종목명·금액·질의 인자가 섞일 수 있다)가
ops_tee 를 거치지 않는 워크플로에서 그대로 공개 로그에 남았다. 공개면 종류·위치만 찍고 1 로 끝낸다. 본문은 비공개
DB 실행 기록(`fail_open_runs`)에 그대로 남는다. 비공개·로컬은 예전처럼 다시 던진다.
"""

from __future__ import annotations

import pytest

from batch.core import db, entry

비밀 = "삼성전자 1,234,567원 보유"


def _main() -> int:
    try:
        raise KeyError(비밀)
    except KeyError as exc:
        raise ValueError(f"평가 실패: {비밀}") from exc


@pytest.fixture
def 닫음(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    남김: list[str] = []
    monkeypatch.setattr(entry, "paused_on_d1", lambda: None)
    monkeypatch.setattr(entry, "deferred_by_read_budget", lambda _name: None)
    monkeypatch.setattr(db, "fail_open_runs", 남김.append)
    return 남김


def test_공개면_본문_없이_종류와_위치만_찍고_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], 닫음: list[str]
) -> None:
    monkeypatch.setenv("REPO_PUBLIC", "true")
    assert entry.guard(_main) == 1
    out = capsys.readouterr()
    글 = out.out + out.err
    assert "삼성전자" not in 글 and "1,234,567" not in 글
    assert "ValueError" in 글 and "← 원인 KeyError" in 글
    assert "test_guard_public_1917.py:" in 글 and "_main" in 글
    assert 닫음 and 비밀 in 닫음[0]  # 본문은 비공개 DB 실행 기록으로


def test_비공개면_예전처럼_다시_던진다(monkeypatch: pytest.MonkeyPatch, 닫음: list[str]) -> None:
    monkeypatch.setenv("REPO_PUBLIC", "false")
    with pytest.raises(ValueError, match="평가 실패"):
        entry.guard(_main)
    assert 닫음


def test_원인_사슬은_다섯까지만() -> None:
    exc: BaseException = RuntimeError("끝")
    for i in range(10):
        new = RuntimeError(f"단계 {i}")
        new.__cause__ = exc
        exc = new
    assert entry.redacted_failure(exc).count("← 원인") == 5
