"""DB 연결을 못 열어도 실패 알림이 나간다 (docs/infra.md 25.1215).

예전에는 `TursoClient()` 가 실패 처리 `try` 밖이라, 시크릿이 빠진 날 기록도 알림도 없이 종료코드 1 만 남았다.
"""

from __future__ import annotations

import pytest

from batch.jobs import daily


def test_연결을_못_열면_실패_알림을_보낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    def 실패() -> None:
        raise RuntimeError("환경변수 TURSO_DATABASE_URL 이(가) 비어 있습니다.")

    monkeypatch.setattr(daily, "TursoClient", 실패)
    보냄: list[str] = []
    monkeypatch.setattr(daily.telegram, "send", lambda m, *a, **k: 보냄.append(m))
    with pytest.raises(Exception):  # noqa: B017 — 원인은 그대로 올라가 main 이 종료코드 1 을 낸다
        daily.run("KR")
    assert len(보냄) == 1
    assert "DB 연결을 열지 못함" in 보냄[0]
    assert "TURSO_DATABASE_URL" not in 보냄[0]  # 예외 글은 싣지 않는다 — 형만
