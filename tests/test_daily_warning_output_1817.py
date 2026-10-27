"""일일 배치가 `partial` 로 닫는 까닭을 운영 출력에 남긴다 (docs/infra.md 25.1817).

2026-10-26 미국·10-27 국내 일일 배치 출력 끝줄이 "완료 (partial)" 인데 어느 주의 때문인지 한 줄도 없었다 — 주의는 리포트와
실행 기록에만 있었다. 운영 출력은 `ops_tee` 를 거쳐 비공개 운영 저장소로만 가므로 거기 찍는다.
"""

import inspect
from pathlib import Path

from batch.jobs import daily

ROOT = Path(__file__).resolve().parent.parent


def test_주의_줄을_자르고_상한을_둔다() -> None:
    assert daily._주의_출력([]) == []
    assert daily._주의_출력(["점수: 재료 1건을 읽지 못한 채 계산했습니다"]) == [
        "  주의: 점수: 재료 1건을 읽지 못한 채 계산했습니다"
    ]
    긴 = daily._주의_출력(["가" * 300], 길이=10)
    assert 긴 == ["  주의: " + "가" * 10 + "…"]
    많음 = daily._주의_출력([f"줄{i}" for i in range(25)], 최대=20)
    assert len(많음) == 21 and 많음[-1] == "  주의 5줄 더 (실행 기록 step_log 참고)"


def test_실행을_닫기_전에_주의를_찍는다() -> None:
    글 = inspect.getsource(daily)
    끝 = 글.index('        for 줄 in _주의_출력(warnings):\n            print(줄)\n        status = "partial" if warnings')
    assert 끝 < 글.index('print(f"완료 ({status}). 종목')


def test_일일_배치는_늘_ops_tee_를_거친다() -> None:
    """주의 줄에는 보유 종목 수 같은 운영 값이 섞일 수 있다 — 공개 로그로 새지 않게 일일 배치를 부르는 줄은 모두 `ops_tee`"""
    줄들 = [
        줄
        for 이름 in ("daily-kr.yml", "daily-us.yml")
        for 줄 in (ROOT / ".github" / "workflows" / 이름).read_text(encoding="utf-8").splitlines()
        if "python -m batch.jobs.daily" in 줄 and not 줄.strip().startswith("#")
    ]
    assert len(줄들) >= 4
    assert all("| python scripts/ops_tee.py" in 줄 for 줄 in 줄들), 줄들
