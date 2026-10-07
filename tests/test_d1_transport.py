"""D1 어댑터의 통신·카운터 (batch/core/d1.py).

**왜 이 테스트가 생겼나.** 2026-09-20 에 덮임을 재 보니 `batch/core/d1.py` 가 54% 였다.
지금 **모든 것이 이 어댑터 위에서 돈다**(Turso 가 잠겨 D1 임시 운영, infra 25절). 그런데
정작 다시 보내기·오류 가르기·카운터 재진입 같은 위험한 자리가 한 번도 검증된 적이 없었다.
9/19~20 에 하루 한도를 두 배 넘긴 일(25.20)이 전부 이 카운터를 믿고 한 계산이었다.

여기서 지키는 것:

1. **4xx 는 다시 보내지 않는다.** 하루 쓰기 한도 오류가 HTTP 400 으로 온다(25.21).
   다시 보내도 같고, `RETRY_DELAYS` 만큼 11초를 버린다. 게다가 **오류 문구가 그대로 살아야**
   `db.quota_reason()` 이 "한도" 로 알아보고 건너뜀으로 바꾼다(25.6)
2. **5xx·429·연결 끊김은 다시 보낸다.** 잠깐 흔들린 것으로 배치를 죽이지 않는다
3. **카운터는 재진입해도 행을 잃지 않는다.** 카운터를 적는 것 자체가 쓰기라서, 적는 동안
   생긴 쓰기를 버리면 **쓴 것보다 적게 세고** 예산 계산이 틀어진다
4. **카운터가 실패해도 본 작업은 죽지 않는다.** 재는 일이 재어지는 일을 망치면 안 된다

네트워크를 타지 않는다. 세션을 가짜로 바꿔 정해진 응답을 돌려준다.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from batch.core import d1 as mod
from batch.core.turso import TursoError


class 가짜응답:
    def __init__(self, status: int, body: Any = None, text: str = "") -> None:
        self.status_code = status
        self._body = body
        self.text = text or str(body)

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise ValueError("JSON 아님")
        return self._body


class 가짜세션:
    """정해진 응답을 차례로 돌려준다. 마지막 것은 계속 반복한다."""

    def __init__(self, *응답들: Any) -> None:
        self.응답들 = list(응답들)
        self.보낸것: list[dict] = []

    def post(self, url: str, json: dict, timeout: int) -> Any:  # noqa: A002
        self.보낸것.append(json)
        응답 = self.응답들[min(len(self.보낸것) - 1, len(self.응답들) - 1)]
        if isinstance(응답, Exception):
            raise 응답
        return 응답

    def close(self) -> None:
        pass


@pytest.fixture
def 클라이언트(monkeypatch: pytest.MonkeyPatch):
    """설정 없이 만든다. 세션은 부르는 쪽에서 갈아 끼운다."""
    def 만들기(*응답들: Any) -> mod.D1Client:
        c = mod.D1Client(account_id="a", database_id="b", token="t")
        c._session = 가짜세션(*응답들)
        return c

    monkeypatch.setattr("time.sleep", lambda _s: None)  # 다시 보내기 기다림을 건너뛴다
    return 만들기


def 성공응답(written: int = 0, read: int = 0, rows: list | None = None) -> 가짜응답:
    return 가짜응답(200, {
        "success": True,
        "result": [{"success": True, "results": rows or [],
                    "meta": {"rows_written": written, "rows_read": read}}],
    })  # fmt: skip


class Test다시_보내기:
    def test_400_은_다시_보내지_않는다(self, 클라이언트) -> None:
        """하루 한도 오류가 400 으로 온다. 다시 보내도 같고 11초를 버린다."""
        본문 = '{"errors":[{"code":7500,"message":"exceeded D1\'s free tier daily row write limit"}]}'
        c = 클라이언트(가짜응답(400, None, 본문))
        with pytest.raises(TursoError):
            c.execute("INSERT INTO t VALUES (?)", [1])
        assert len(c._session.보낸것) == 1, "400 인데 다시 보냈다"

    def test_400_의_문구가_살아_있어야_한도로_알아본다(self, 클라이언트) -> None:
        """이 문구가 잘리면 guard() 가 한도를 못 알아보고 배치가 실패로 끝난다 (infra 25.6)."""
        from batch.core import db

        본문 = '{"errors":[{"message":"Your account has exceeded D1\'s free tier daily row write limit."}]}'
        c = 클라이언트(가짜응답(400, None, 본문))
        with pytest.raises(TursoError) as 잡힘:
            c.execute("INSERT INTO t VALUES (?)", [1])
        assert db.quota_reason(잡힘.value) is not None

    @pytest.mark.parametrize("status", [401, 403, 404])
    def test_다른_4xx_도_한_번만(self, 클라이언트, status: int) -> None:
        c = 클라이언트(가짜응답(status, None, "안 됨"))
        with pytest.raises(TursoError):
            c.execute("SELECT 1")
        assert len(c._session.보낸것) == 1

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_5xx_와_429_는_다시_보낸다(self, 클라이언트, status: int) -> None:
        c = 클라이언트(가짜응답(status, None, "잠깐"))
        with pytest.raises(TursoError):
            c.execute("SELECT 1")
        assert len(c._session.보낸것) == len(mod.RETRY_DELAYS) + 1

    def test_다시_보내서_성공하면_돌아온다(self, 클라이언트) -> None:
        c = 클라이언트(가짜응답(503, None, "잠깐"), 성공응답(read=3))
        c.execute("SELECT 1")
        assert len(c._session.보낸것) == 2

    def test_연결이_끊겨도_다시_보낸다(self, 클라이언트) -> None:
        c = 클라이언트(requests.ConnectionError("끊김"), 성공응답())
        c.execute("SELECT 1")
        assert len(c._session.보낸것) == 2

    def test_200_인데_JSON_이_아니면_말해_준다(self, 클라이언트) -> None:
        c = 클라이언트(가짜응답(200, ValueError(), "<html>"))
        with pytest.raises(TursoError, match="해석하지 못"):
            c.execute("SELECT 1")


class Test응답_읽기:
    def test_배치의_행_수를_합산한다(self, 클라이언트) -> None:
        본문 = {"success": True, "result": [
            {"success": True, "results": [], "meta": {"rows_written": 10, "rows_read": 1}},
            {"success": True, "results": [], "meta": {"rows_written": 5, "rows_read": 2}},
        ]}  # fmt: skip
        c = 클라이언트(가짜응답(200, 본문))
        흘려보낸것: list[tuple[int, int]] = []
        c._flush = lambda w, r: 흘려보낸것.append((w, r))  # type: ignore[assignment]
        c.batch([("INSERT INTO t VALUES (?)", [1]), ("INSERT INTO t VALUES (?)", [2])])

        # 센 것은 **흘려보낸 것 + 아직 보류인 것**이다. 문턱(flush_rows) 아래면 보류에 남는다.
        # 둘을 합쳐 봐야 "하나도 잃지 않았는가" 를 본다 — 이것이 예산 계산의 전제다
        쓴것 = sum(w for w, _ in 흘려보낸것) + c.writes.pending
        읽은것 = sum(r for _, r in 흘려보낸것) + c.reads.pending
        assert 쓴것 == 15, f"쓰기 10+5 를 합쳐 15 여야 하는데 {쓴것}"
        assert 읽은것 == 3, f"읽기 1+2 를 합쳐 3 이어야 하는데 {읽은것}"

    def test_문장_하나가_실패하면_터진다(self, 클라이언트) -> None:
        본문 = {"success": True, "result": [{"success": False, "error": "no such table: t"}]}
        c = 클라이언트(가짜응답(200, 본문))
        with pytest.raises(TursoError, match="SQL 실패"):
            c.execute("SELECT 1 FROM t")

    def test_전체가_실패하면_터진다(self, 클라이언트) -> None:
        c = 클라이언트(가짜응답(200, {"success": False, "errors": [{"message": "권한 없음"}]}))
        with pytest.raises(TursoError, match="D1 실패"):
            c.execute("SELECT 1")


class Test카운터_재진입:
    """카운터를 적는 것 자체가 쓰기다. 적는 동안 생긴 쓰기를 버리면 쓴 것보다 적게 센다."""

    def test_적는_중에_생긴_쓰기를_보류에_모은다(self, 클라이언트) -> None:
        c = 클라이언트(성공응답())
        c._recording = True
        c._flush(7, 3)
        assert c.writes.pending == 7 and c.reads.pending == 3

    def test_보류가_쌓이고_잃지_않는다(self, 클라이언트) -> None:
        c = 클라이언트(성공응답())
        c._recording = True
        c._flush(7, 0)
        c._flush(5, 0)
        assert c.writes.pending == 12

    def test_0_이면_아무것도_하지_않는다(self, 클라이언트) -> None:
        c = 클라이언트(성공응답())
        c._recording = True
        c._flush(0, 0)
        assert c.writes.pending == 0

    def test_음수는_보류에_넣지_않는다(self, 클라이언트) -> None:
        """meta 가 이상한 값을 줘도 카운터가 뒤로 가면 안 된다."""
        c = 클라이언트(성공응답())
        c._recording = True
        c._flush(-5, 0)
        assert c.writes.pending == 0

    def test_카운터가_실패해도_본_작업은_안_죽는다(
        self, 클라이언트, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from batch.core import db

        monkeypatch.setattr(db, "record_rows_written", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("DB 없음")))
        c = 클라이언트(성공응답())
        c._flush(10, 0)  # 예외가 올라오면 안 된다
        assert c._recording is False, "실패한 뒤 재진입 표시가 남으면 다음 기록이 통째로 막힌다"
