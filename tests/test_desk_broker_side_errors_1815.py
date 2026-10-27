"""KIS 비교의 곁다리 실패도 아침 운용 점검에 (docs/infra.md 25.1815, 25.1812 교차검증 [권고]).

`defer_mark_error`(시세·환율 구멍으로 미룬 매도 표시, 25.1811)·`cells_error`(체결 칸)는 실행 기록에만 있어, 그날 괴리
알림이 미룬 매도 탓인지·체결 칸이 왜 안 바뀌었는지 알 길이 없었다.
"""

from __future__ import annotations

from batch.services import desk


def _확인(b: dict) -> list[str]:
    return [x["text"] for x in desk.checks(halted=False, paper=None, broker=b, recon_bad=[], warnings=0)]


def test_표시_실패와_체결_칸_실패를_말한다() -> None:
    글 = " / ".join(_확인({"defer_mark_error": "RuntimeError", "cells_error": "ValueError"}))
    assert "KIS 비교 표본 표시 실패(RuntimeError)" in 글 and "미룬 매도가 섞였을 수" in 글
    assert "KIS 체결 칸 계산 실패(ValueError)" in 글


def test_실패가_없으면_말하지_않는다() -> None:
    글 = " / ".join(_확인({}))
    assert "표본 표시 실패" not in 글 and "체결 칸 계산 실패" not in 글
