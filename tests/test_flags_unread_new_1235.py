"""매도 플래그를 못 읽고 보낸 리포트는 플래그를 "보여 준" 것으로 치지 않는다 (docs/infra.md 25.1235, 매도 플래그 감사)."""

from __future__ import annotations

import json

import pytest

from batch.jobs import daily
from tests.test_report_subjob_warnings import _NEW


def test_플래그_절이_빠진_리포트_뒤에도_NEW() -> None:
    빠짐 = json.dumps([f"{daily.FLAGS_UNREAD_MARK} 리포트에 싣지 못했습니다 — 화면에서 확인하세요 (시간 초과)"],
                    ensure_ascii=False)  # fmt: skip
    # 9/24 08:25 에 걸렸고 08:30 리포트는 플래그를 못 읽고 갔다 → 앞서 보낸 9/23 리포트가 경계다
    assert _NEW("2026-09-24", "2026-09-23T23:25:00+00:00", "2026-09-23T23:30:00+00:00", 빠짐,
                앞_발송="2026-09-22T23:30:00+00:00") is True  # fmt: skip


def test_경고_글이_표식을_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import sell_flags

    def 실패(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("시간 초과")

    monkeypatch.setattr(sell_flags, "active_flags_for_report", 실패)
    경고: list[str] = []
    assert daily._sell_flags_for_report(object(), "KR", 경고) == []  # type: ignore[arg-type]
    assert 경고 and 경고[0].startswith(daily.FLAGS_UNREAD_MARK)
