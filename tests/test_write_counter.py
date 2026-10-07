"""쓰기 행 카운터 (docs/infra.md 23절).

한도를 넘겨 DB 가 잠긴 사고 뒤에 둔 안전장치다. **세는 곳이 하나여야** 실제 값이 나온다.
"""

from __future__ import annotations

from typing import Any

import pytest

from batch.core import turso


class Test모으기:
    def test_문턱에_닿아야_내보낸다(self) -> None:
        counter = turso.WriteCounter(flush_rows=100)
        assert counter.add(40) == 0
        assert counter.add(40) == 0
        assert counter.add(40) == 120  # 쌓인 것을 한 번에
        assert counter.pending == 0
        assert counter.total == 120

    def test_읽기는_세지_않는다(self) -> None:
        counter = turso.WriteCounter()
        assert counter.add(0) == 0 and counter.add(-3) == 0
        assert counter.total == 0

    def test_남은_것은_끝낼_때_가져간다(self) -> None:
        counter = turso.WriteCounter(flush_rows=100)
        counter.add(7)
        assert counter.take() == 7
        assert counter.take() == 0  # 두 번 가져가도 두 번 세지 않는다


class Test클라이언트:
    """응답의 affected_row_count 를 더한다. HTTP 는 타지 않는다."""

    def _client(self, monkeypatch: pytest.MonkeyPatch, affected: list[int]) -> turso.TursoClient:
        monkeypatch.setattr(turso.config, "require", lambda key: "https://x.turso.io" if "URL" in key else "t")

        class FakeResponse:
            status_code = 200

            def json(self) -> dict[str, Any]:
                return {
                    "results": [
                        {"type": "ok", "response": {"type": "execute", "result": {"affected_row_count": n}}}
                        for n in affected
                    ]
                }

        client = turso.TursoClient()
        monkeypatch.setattr(client, "_post_with_retry", lambda payload: FakeResponse())
        return client

    def test_응답의_영향_행을_더한다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client(monkeypatch, [3, 0, 7])  # 가운데는 SELECT
        recorded: list[int] = []
        monkeypatch.setattr(client, "_flush_writes", recorded.append)
        client.batch([("INSERT ...", []), ("SELECT ...", []), ("UPDATE ...", [])])
        assert client.writes.total == 10

    def test_문턱을_넘으면_기록한다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client(monkeypatch, [turso.WRITE_FLUSH_ROWS])
        seen: list[int] = []
        monkeypatch.setattr(client, "_flush_writes", seen.append)
        client.batch([("INSERT ...", [])])
        assert seen == [turso.WRITE_FLUSH_ROWS]

    def test_기록하는_동안은_다시_세지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """카운터를 적는 UPDATE 도 쓰기다. 그것을 또 세면 끝없이 돈다."""
        client = self._client(monkeypatch, [1])
        client._recording = True
        called: list[int] = []
        monkeypatch.setattr(turso.TursoClient, "_flush_writes", turso.TursoClient._flush_writes)

        from batch.core import db

        monkeypatch.setattr(db, "record_rows_written", lambda c, rows: called.append(rows))
        client.writes.pending = 0
        client._flush_writes(10)
        assert called == []  # 적는 중에는 건너뛴다
        assert client.writes.pending == 10  # 버리지 않는다. 다음 기회에 함께 적는다
        client._recording = False
        client._flush_writes(client.writes.take())
        assert called == [10]

    def test_기록이_실패해도_본_작업을_막지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client(monkeypatch, [2])
        from batch.core import db

        def boom(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("DB 가 막혔다")

        monkeypatch.setattr(db, "record_rows_written", boom)
        client._flush_writes(1)  # 예외가 새어 나오지 않는다
        assert client.batch([("INSERT ...", [])])[0].affected_rows == 2
