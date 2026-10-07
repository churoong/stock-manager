"""Turso 프로토콜 값 변환 테스트.

네트워크를 타지 않는다.
값에 타입이 붙어 오가고 정수가 문자열로 실려 오기 때문에,
여기서 틀리면 모든 수치가 조용히 어긋난다.
"""

from __future__ import annotations

import pytest

from batch.core.turso import ResultSet, _decode, _encode, _host, _to_result_set


class Test호스트추출:
    @pytest.mark.parametrize(
        "url",
        [
            "libsql://내디비-계정.region.turso.io",
            "https://내디비-계정.region.turso.io",
            "libsql://내디비-계정.region.turso.io/",
            "wss://내디비-계정.region.turso.io",
        ],
    )
    def test_어떤_스킴이든_호스트만_남는다(self, url: str) -> None:
        assert _host(url) == "내디비-계정.region.turso.io"

    def test_앞뒤_공백을_떼어낸다(self) -> None:
        assert _host("  libsql://a.turso.io  ") == "a.turso.io"


class Test값인코딩:
    def test_None_은_null_이다(self) -> None:
        assert _encode(None) == {"type": "null"}

    def test_정수는_문자열로_실린다(self) -> None:
        assert _encode(42) == {"type": "integer", "value": "42"}

    def test_큰_정수도_정확히_보존된다(self) -> None:
        # 거래대금은 조 단위라 부동소수로 가면 값이 망가진다
        big = 2_849_221_682_500
        assert _encode(big) == {"type": "integer", "value": "2849221682500"}

    def test_bool_은_정수로_보낸다(self) -> None:
        # 파이썬에서 bool 은 int 의 하위형이라 순서를 틀리면 잘못 인코딩된다
        assert _encode(True) == {"type": "integer", "value": "1"}
        assert _encode(False) == {"type": "integer", "value": "0"}

    def test_실수는_그대로_보낸다(self) -> None:
        assert _encode(1.5) == {"type": "float", "value": 1.5}

    def test_문자열은_text_이다(self) -> None:
        assert _encode("삼성전자") == {"type": "text", "value": "삼성전자"}

    def test_바이트는_base64_로_보낸다(self) -> None:
        assert _encode(b"ab") == {"type": "blob", "base64": "YWI="}


class Test값디코딩:
    def test_정수_문자열을_정수로_되돌린다(self) -> None:
        assert _decode({"type": "integer", "value": "2849221682500"}) == 2849221682500

    def test_null_은_None_이다(self) -> None:
        assert _decode({"type": "null"}) is None

    def test_실수(self) -> None:
        assert _decode({"type": "float", "value": 1.5}) == 1.5

    def test_문자열(self) -> None:
        assert _decode({"type": "text", "value": "가"}) == "가"

    def test_바이트(self) -> None:
        assert _decode({"type": "blob", "base64": "YWI="}) == b"ab"


@pytest.mark.parametrize("value", [0, 1, -1, 2_849_221_682_500, 3.14, "가나다", None, True])
def test_인코딩_후_디코딩하면_원래대로(value: object) -> None:
    restored = _decode(_encode(value))
    if isinstance(value, bool):
        # bool 은 정수로 왕복한다. 이것이 의도된 동작이다.
        assert restored == int(value)
    else:
        assert restored == value


class Test결과집합:
    def test_실제_응답_형태를_해석한다(self) -> None:
        raw = {
            "cols": [{"name": "isu_cd"}, {"name": "close"}],
            "rows": [
                [{"type": "text", "value": "005930"}, {"type": "integer", "value": "248500"}]
            ],
            "affected_row_count": 0,
            "last_insert_rowid": None,
        }
        rs = _to_result_set(raw)

        assert rs.columns == ["isu_cd", "close"]
        assert rs.rows == [("005930", 248500)]
        assert len(rs) == 1

    def test_딕셔너리로_바꾼다(self) -> None:
        rs = ResultSet(columns=["a", "b"], rows=[(1, "x")])
        assert rs.dicts() == [{"a": 1, "b": "x"}]

    def test_스칼라는_첫_행_첫_열(self) -> None:
        rs = ResultSet(columns=["cnt"], rows=[(7,)])
        assert rs.scalar() == 7

    def test_빈_결과의_스칼라는_None(self) -> None:
        rs = ResultSet(columns=["cnt"], rows=[])
        assert rs.scalar() is None
        assert len(rs) == 0

    def test_last_insert_rowid_를_정수로_바꾼다(self) -> None:
        rs = _to_result_set({"cols": [], "rows": [], "last_insert_rowid": "15"})
        assert rs.last_insert_rowid == 15


class Test일시실패_재시도:
    """2026-09-17 백필이 Turso 502 한 번에 통째로 죽었다. 일시 실패만 다시 보낸다."""

    class _Resp:
        def __init__(self, status: int) -> None:
            self.status_code = status
            self.text = ""

        def json(self) -> dict:
            return {"results": []}

    def _client(self, monkeypatch, outcomes):
        import requests

        from batch.core import turso

        monkeypatch.setattr(turso, "RETRY_DELAYS", (0, 0, 0))
        client = turso.TursoClient("libsql://example.turso.io", "token")
        calls = []

        def fake_post(*_args, **_kwargs):
            calls.append(1)
            item = outcomes[len(calls) - 1]
            if isinstance(item, Exception):
                raise item
            return self._Resp(item)

        monkeypatch.setattr(client._session, "post", fake_post)
        return client, calls, requests

    def test_502_뒤_성공하면_그대로_돌려준다(self, monkeypatch) -> None:
        client, calls, _ = self._client(monkeypatch, [502, 200])
        assert client._post_with_retry({}).status_code == 200
        assert len(calls) == 2

    def test_타임아웃도_다시_보낸다(self, monkeypatch) -> None:
        import requests

        client, calls, _ = self._client(monkeypatch, [requests.ReadTimeout("느림"), 200])
        assert client._post_with_retry({}).status_code == 200
        assert len(calls) == 2

    def test_401_은_다시_보내지_않는다(self, monkeypatch) -> None:
        client, calls, _ = self._client(monkeypatch, [401, 200])
        assert client._post_with_retry({}).status_code == 401
        assert len(calls) == 1

    def test_계속_실패하면_마지막_응답을_돌려준다(self, monkeypatch) -> None:
        client, calls, _ = self._client(monkeypatch, [502, 502, 502, 502])
        assert client._post_with_retry({}).status_code == 502
        assert len(calls) == 4
