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
    끝 = 글.index('            for 줄 in _주의_출력(warnings):\n                print(줄)\n        except Exception as exc:')
    assert 글.index('status = "partial" if warnings else "success"\n        try:\n            db.finish_batch_run') > 끝
    assert 끝 < 글.index('print(f"완료 ({status}). 종목')


def test_일일_배치는_늘_ops_tee_를_거친다() -> None:
    """주의 줄에는 보유 종목 수 같은 운영 값이 섞일 수 있다 — 공개 로그로 새지 않게 일일 배치를 부르는 줄은 모두 `ops_tee`.
    모든 워크플로를 훑는다 (25.1818, 교차검증 [참고] — 다른 워크플로가 일일 배치를 부르게 되어도 잡게)"""
    줄들 = [
        (p.name, 줄)
        for p in sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        for 줄 in p.read_text(encoding="utf-8").splitlines()
        if "python -m batch.jobs.daily" in 줄 and not 줄.strip().startswith("#")
    ]
    assert len(줄들) >= 4
    assert all("| python scripts/ops_tee.py" in 줄 for _, 줄 in 줄들), 줄들


def test_줄바꿈과_짝_없는_서로게이트를_한_줄로_찍는다() -> None:
    """25.1818 (25.1817 교차검증 [권고]) — Actions stdout 은 strict utf-8 이라 짝 없는 서로게이트가 print 를 깨뜨렸다"""
    import io

    줄 = daily._주의_출력(["KIS 실패: {\"m\": \"\ud83d\"}\n실행하지 않음: 거짓"])
    assert len(줄) == 1 and "\n" not in 줄[0] and " ⏎ 실행하지 않음" in 줄[0]
    엄격 = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    엄격.write(줄[0])  # 깨지지 않는다
