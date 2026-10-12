"""Turso 통신 오류 글에 DB 호스트가 실리지 않는다 (docs/infra.md 25.1218).

시세 사본 맞추기 단계는 `ops_tee` 를 거치지 않아 이 글이 공개 Actions 로그에 찍힌다.
"""

from __future__ import annotations

import logging

import pytest
import requests

from batch.core import turso

HOST = "secret-db-name-org.turso.io"


def test_통신_실패_글에_호스트가_없다(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(turso, "RETRY_DELAYS", [0])
    c = turso.TursoClient(database_url=f"libsql://{HOST}", auth_token="t")

    def 끊김(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise requests.ConnectionError(f"HTTPSConnectionPool(host='{HOST}', port=443): Max retries exceeded")

    monkeypatch.setattr(c._session, "post", 끊김)
    with caplog.at_level(logging.WARNING), pytest.raises(turso.TursoError) as e:
        c.execute("SELECT 1")
    assert HOST not in str(e.value)
    assert "<db-host>" in str(e.value)
    assert HOST not in caplog.text
