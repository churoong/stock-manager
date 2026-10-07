"""텔레그램 호출의 다시 보내기 (`batch/notify/telegram._call`).

**왜 있나.** 2026-09-20 에 덮임을 재 보니 `batch/notify/telegram.py` 가 52% 였다.
검증된 것은 `split_message` 뿐이고, **모든 알림이 지나가는 다시 보내기 고리**는 한 번도
돌아 본 적이 없었다.

거기서 하나가 나왔다. **429 가 시키는 만큼 무조건 잤다.**

```python
wait = int(body.get("parameters", {}).get("retry_after", 5))
time.sleep(wait + 1)
```

`retry_after` 는 **바깥이 정하는 값**이다. 텔레그램이 한 시간을 부르면 한 시간을 잔다.
네 번이면 네 시간이고, GitHub Actions 는 **자는 동안에도 분을 센다** — 그것만으로 월
무료 분(2,000분)의 12% 다. 9/20 에 무료 분이 바닥났다(infra 25.27). 이것이 원인이라는
증거는 없지만 `[확인필요]`, 바깥이 우리 예산을 정하게 두는 구멍을 열어 둘 이유가 없다.

함께 나온 것: 모양을 믿고 `int()` 를 그냥 불렀다. `parameters` 가 dict 가 아니거나
`retry_after` 가 null 이면 **알림을 보내다가 배치가 죽는다.**

네트워크를 타지 않는다. `requests.post` 와 `time.sleep` 을 가짜로 바꾼다.
"""

from __future__ import annotations

from typing import Any

import pytest

from batch.notify import telegram as tg


class 가짜응답:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture
def 부르기(monkeypatch: pytest.MonkeyPatch):
    """정해진 응답을 차례로 돌려준다. 마지막 것은 계속 반복한다."""
    잔시간: list[float] = []
    보낸것: list[dict] = []

    def 만들기(*응답들: Any):
        monkeypatch.setattr(tg.config, "require", lambda _k: "토큰")
        monkeypatch.setattr(tg.time, "sleep", 잔시간.append)

        def post(url: str, json: dict, timeout: int) -> Any:  # noqa: A002
            보낸것.append(json)
            응답 = 응답들[min(len(보낸것) - 1, len(응답들) - 1)]
            if isinstance(응답, Exception):
                raise 응답
            return 응답

        monkeypatch.setattr(tg.requests, "post", post)
        return 보낸것, 잔시간

    return 만들기


def 성공(message_id: int = 1) -> 가짜응답:
    return 가짜응답(200, {"ok": True, "result": {"message_id": message_id}})


def 제한(seconds: Any) -> 가짜응답:
    return 가짜응답(429, {"ok": False, "parameters": {"retry_after": seconds}})


class Test_retry_after_읽기:
    """바깥이 주는 값이다. 모양을 믿지 않는다."""

    def test_정상(self) -> None:
        assert tg.retry_after({"parameters": {"retry_after": 12}}) == 12

    def test_글자로_와도_읽는다(self) -> None:
        assert tg.retry_after({"parameters": {"retry_after": "12"}}) == 12

    @pytest.mark.parametrize(
        "본문",
        [
            {},
            {"parameters": None},
            {"parameters": "이상함"},
            {"parameters": {}},
            {"parameters": {"retry_after": None}},
            {"parameters": {"retry_after": "곧"}},
            {"parameters": {"retry_after": -5}},
        ],
    )
    def test_이상하면_기본값으로_돌아간다(self, 본문: dict) -> None:
        # 여기서 터지면 알림을 보내다가 배치가 죽는다
        assert tg.retry_after(본문) == tg.DEFAULT_RETRY_AFTER


class Test호출_제한:
    def test_시키는_만큼_기다렸다_다시_보낸다(self, 부르기) -> None:
        보낸것, 잔시간 = 부르기(제한(3), 성공())

        tg._call("sendMessage", {"text": "안녕"})

        assert len(보낸것) == 2
        assert 잔시간 == [4]  # retry_after + 1

    def test_너무_오래_기다리라면_기다리지_않는다(self, 부르기) -> None:
        # **이것이 이 파일이 생긴 이유다.** 자는 동안에도 Actions 분이 흐른다
        보낸것, 잔시간 = 부르기(제한(tg.MAX_RETRY_AFTER + 1))

        with pytest.raises(tg.TelegramError, match="기다리지 않습니다"):
            tg._call("sendMessage", {"text": "안녕"})

        assert 잔시간 == [], "한 번도 자면 안 된다"
        assert len(보낸것) == 1, "포기했는데 또 보냈다"

    def test_상한까지는_기다린다(self, 부르기) -> None:
        # 경계에서 조용히 포기해 버리면 잠깐의 제한에도 알림을 잃는다
        _보낸것, 잔시간 = 부르기(제한(tg.MAX_RETRY_AFTER), 성공())

        tg._call("sendMessage", {"text": "안녕"})

        assert 잔시간 == [tg.MAX_RETRY_AFTER + 1]

    def test_마지막_차례에는_자지_않는다(self, 부르기) -> None:
        # 자고 나서 다시 보내지도 않을 거면 그 시간은 통째로 낭비다
        _보낸것, 잔시간 = 부르기(제한(10))

        with pytest.raises(tg.TelegramError):
            tg._call("sendMessage", {"text": "안녕"})

        assert len(잔시간) == tg.MAX_RETRY - 1, f"잔 횟수가 {len(잔시간)}"

    def test_상한이_분_단위로_아프지_않은_값이다(self) -> None:
        """**숫자 자체를 못 박는다.** 위 테스트들은 `MAX_RETRY_AFTER` 를 기준으로 재므로
        상한을 한 시간으로 올려도 통과한다 — "상한이 있다" 는 증명하지만 "상한이 쓸모
        있다" 는 증명하지 못한다. Actions 는 **분 단위**로 세니 여기서 분으로 따진다.
        """
        최악 = tg.MAX_RETRY_AFTER * (tg.MAX_RETRY - 1)

        assert 최악 <= 180, (
            f"한 실행이 자는 데만 최대 {최악}초({최악 / 60:.0f}분)를 쓴다."
            " Actions 무료 분을 태운다 (infra 25.38)"
        )

    def test_계속_제한이면_끝내_포기한다(self, 부르기) -> None:
        보낸것, _잔시간 = 부르기(제한(1))

        with pytest.raises(tg.TelegramError, match="재시도 횟수"):
            tg._call("sendMessage", {"text": "안녕"})

        assert len(보낸것) == tg.MAX_RETRY


class Test서버_오류와_통신_오류:
    def test_5xx_는_다시_보낸다(self, 부르기) -> None:
        보낸것, _ = 부르기(가짜응답(503, {"ok": False}), 성공())

        tg._call("sendMessage", {"text": "안녕"})

        assert len(보낸것) == 2

    def test_5xx_가_이어지면_포기한다(self, 부르기) -> None:
        보낸것, _ = 부르기(가짜응답(500, {"ok": False}))

        with pytest.raises(tg.TelegramError, match="서버 오류 500"):
            tg._call("sendMessage", {"text": "안녕"})

        assert len(보낸것) == tg.MAX_RETRY

    @staticmethod
    def _연결_안_됨():
        """요청이 나가기 전의 실패 — urllib3 가 `NewConnectionError` 를 이유로 싸서 올린다."""
        import requests
        from urllib3.exceptions import MaxRetryError, NewConnectionError

        return requests.ConnectionError(MaxRetryError(None, "/", NewConnectionError(None, "연결 거부")))

    def test_연결이_안_되면_다시_보낸다(self, 부르기) -> None:
        보낸것, _ = 부르기(self._연결_안_됨(), 성공())

        tg._call("sendMessage", {"text": "안녕"})

        assert len(보낸것) == 2

    def test_보낸_뒤_끊기면_다시_보내지_않고_모른다고_한다(self, 부르기) -> None:
        """요청을 읽은 뒤 끊기면 텔레그램이 이미 보냈을 수 있다 (docs/infra.md 25.613, 감사)."""
        import requests

        보낸것, _ = 부르기(requests.ConnectionError("Connection aborted. RemoteDisconnected"), 성공())

        with pytest.raises(tg.TelegramUncertainError):
            tg._call("sendMessage", {"text": "안녕"})

        assert len(보낸것) == 1

    def test_보내기가_아니면_끊겨도_다시_부른다(self, 부르기) -> None:
        import requests

        보낸것, _ = 부르기(requests.ConnectionError("끊김"), 성공())

        tg._call("getUpdates", {})

        assert len(보낸것) == 2

    def test_계속_끊기면_포기하고_이유를_말한다(self, 부르기) -> None:
        보낸것, 잔시간 = 부르기(self._연결_안_됨())

        with pytest.raises(tg.TelegramError, match="호출 실패"):
            tg._call("sendMessage", {"text": "안녕"})

        assert len(보낸것) == tg.MAX_RETRY
        # 마지막 차례에는 자지 않는다. 2+4+8 = 14초
        assert 잔시간 == [2, 4, 8]

    def test_JSON_이_아니어도_죽지_않는다(self, 부르기) -> None:
        부르기(가짜응답(200, ValueError("JSON 아님")))

        # ok 를 못 읽으면 실패로 본다(25.757 에 되돌림). 여기서 예외가 새면 배치가 죽는다
        with pytest.raises(tg.TelegramError, match="응답 없음"):
            tg._call("sendMessage", {"text": "안녕"})


class Test잘못된_요청:
    def test_4xx_는_다시_보내지_않는다(self, 부르기) -> None:
        # chat_id 가 틀렸거나 토큰이 죽었다. 다시 보내도 같고 시간만 버린다
        보낸것, 잔시간 = 부르기(가짜응답(400, {"ok": False, "description": "chat not found"}))

        with pytest.raises(tg.TelegramError, match="chat not found"):
            tg._call("sendMessage", {"text": "안녕"})

        assert len(보낸것) == 1
        assert 잔시간 == []

    def test_오류_문구에_토큰이_들어가지_않는다(self, 부르기) -> None:
        부르기(가짜응답(401, {"ok": False, "description": "Unauthorized"}))

        with pytest.raises(tg.TelegramError) as 잡힘:
            tg._call("sendMessage", {"text": "안녕"})

        # 이 예외는 로그와 실패 알림에 그대로 실린다
        assert "토큰" not in str(잡힘.value)


class Test보내기:
    def test_나뉜_통마다_message_id_를_모은다(self, 부르기, monkeypatch) -> None:
        monkeypatch.setattr(tg.config, "get", lambda _k, *a: "채팅")
        보낸것, _ = 부르기(성공(7))

        ids = tg.send("안녕")

        assert ids == [7]
        assert len(보낸것) == 1

    def test_고지가_없으면_붙인다(self, 부르기, monkeypatch) -> None:
        monkeypatch.setattr(tg.config, "get", lambda _k, *a: "채팅")
        monkeypatch.setattr(tg.config, "TELEGRAM_DISCLAIMER", True)  # 켰을 때 — 기본은 꺼짐(25.878)
        보낸것, _ = 부르기(성공())

        tg.send("안녕")

        assert 보낸것[0]["text"].endswith(tg.config.DISCLAIMER)

    def test_이미_있으면_두_번_붙이지_않는다(self, 부르기, monkeypatch) -> None:
        monkeypatch.setattr(tg.config, "get", lambda _k, *a: "채팅")
        보낸것, _ = 부르기(성공())

        tg.send(f"안녕\n\n{tg.config.DISCLAIMER}")

        assert 보낸것[0]["text"].count(tg.config.DISCLAIMER) == 1


class Test채팅방_찾기:
    def test_설정이_있으면_그대로_쓴다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg.config, "get", lambda _k, *a: "12345")

        assert tg.resolve_chat_id() == "12345"

    def test_설정이_없으면_최근_대화방으로_보내지_않는다(self, 부르기, monkeypatch) -> None:
        """모르는 사람이 봇에게 말을 걸면 그 사람에게 리포트가 갔다 (docs/infra.md 25.392)."""
        monkeypatch.setattr(tg.config, "get", lambda _k, *a: None)
        부르기(가짜응답(200, {"ok": True, "result": [{"message": {"chat": {"id": 999}}}]}))

        with pytest.raises(tg.TelegramError, match="보내지 않습니다") as 오류:
            tg.resolve_chat_id()
        assert "999" in str(오류.value)  # 설정을 돕도록 후보는 보여 준다

    def test_채널_글도_후보로_보여_준다(self, 부르기, monkeypatch) -> None:
        부르기(가짜응답(200, {"ok": True, "result": [{"channel_post": {"chat": {"id": -100}}}]}))

        assert tg.discover_chat_ids() == ["-100"]

    def test_후보도_없으면_보내지_않는다고만_말한다(self, 부르기, monkeypatch) -> None:
        monkeypatch.setattr(tg.config, "get", lambda _k, *a: None)
        부르기(가짜응답(200, {"ok": True, "result": []}))

        with pytest.raises(tg.TelegramError, match="TELEGRAM_CHAT_ID 가 비어"):
            tg.resolve_chat_id()


class Test_읽기_시간_초과는_다시_보내지_않는다:
    """요청은 건너갔고 답만 못 받았다. 다시 보내면 같은 조각이 두 번 간다 (docs/infra.md 25.394)."""

    def test_sendMessage_는_한_번만_보내고_실패로_올린다(self, 부르기) -> None:
        import requests

        보낸것, _ = 부르기(requests.ReadTimeout("답 없음"), 성공())
        with pytest.raises(tg.TelegramUncertainError, match="중복 방지"):  # "모름" 으로 올린다 (25.493)
            tg._call("sendMessage", {"text": "리포트"})
        assert len(보낸것) == 1

    def test_연결_시간_초과는_다시_보낸다(self, 부르기) -> None:
        import requests

        보낸것, _ = 부르기(requests.ConnectTimeout("연결 안 됨"), 성공(7))
        assert tg._call("sendMessage", {"text": "리포트"})["result"]["message_id"] == 7
        assert len(보낸것) == 2

    def test_읽기_전용_호출은_다시_부른다(self, 부르기) -> None:
        import requests

        보낸것, _ = 부르기(requests.ReadTimeout("답 없음"), 성공())
        tg._call("getUpdates", {"limit": 10})
        assert len(보낸것) == 2


class Test뒤_조각이_실패하면_보낸_것을_들고_올린다:
    """앞 조각은 이미 갔는데 id 를 버려, 다음 실행이 전체를 다시 보냈다 (docs/infra.md 25.414)."""

    def test_두번째_조각_실패(self, 부르기, monkeypatch: pytest.MonkeyPatch) -> None:
        보낸것, _ = 부르기(성공(11), 가짜응답(400, {"ok": False, "description": "bad"}))
        monkeypatch.setattr(tg, "resolve_chat_id", lambda: "1")
        with pytest.raises(tg.TelegramPartialError) as 잡힘:
            tg.send("가" * 3000 + "\n" + "나" * 3000, with_disclaimer=False)
        assert 잡힘.value.sent_ids == [11] and 잡힘.value.total == 2
        assert len(보낸것) == 2

    def test_첫_조각부터_실패하면_보통의_실패(self, 부르기, monkeypatch: pytest.MonkeyPatch) -> None:
        부르기(가짜응답(400, {"ok": False, "description": "bad"}))
        monkeypatch.setattr(tg, "resolve_chat_id", lambda: "1")
        with pytest.raises(tg.TelegramError) as 잡힘:
            tg.send("짧은 글", with_disclaimer=False)
        assert not isinstance(잡힘.value, tg.TelegramPartialError)


def test_첫_조각이_모름이어도_나머지_조각은_보낸다(monkeypatch) -> None:
    """2부·매도 플래그·고지가 담긴 뒤 조각이 확실히 안 갔는데 "모름" 이라고만 했다 (docs/infra.md 25.567, 감사)."""
    호출: list[str] = []

    def 가짜(method, payload, **k):
        호출.append(payload["text"][:5])
        if len(호출) == 1:
            raise tg.TelegramUncertainError("응답 시간 초과")
        return {"result": {"message_id": len(호출)}}

    monkeypatch.setattr(tg, "_call", 가짜)
    monkeypatch.setattr(tg, "split_message", lambda text: ["첫조각", "둘째조각", "셋째조각"])
    monkeypatch.setattr(tg, "resolve_chat_id", lambda: "1")
    with pytest.raises(tg.TelegramUncertainError, match="확실히 간 조각: 2번째, 3번째") as 오류:
        tg.send("리포트")
    assert len(호출) == 3
    assert 오류.value.sent_ids == [2, 3]  # 일일 배치가 발송 시각을 적는다 (25.570)


def test_첫_조각이_모름이면_뒤_실패도_모름이다(monkeypatch) -> None:
    """날것의 실패로 올려 배치가 failed 로 닫고 다음 트리거가 전체를 다시 보냈다 (docs/infra.md 25.568, 교차검증)."""
    호출: list[int] = []

    def 가짜(method, payload, **k):
        호출.append(1)
        if len(호출) == 1:
            raise tg.TelegramUncertainError("응답 시간 초과")
        if len(호출) == 2:
            raise tg.TelegramError("400 잘못된 요청")
        return {"result": {"message_id": 3}}

    monkeypatch.setattr(tg, "_call", 가짜)
    monkeypatch.setattr(tg, "split_message", lambda text: ["가", "나", "다"])
    monkeypatch.setattr(tg, "resolve_chat_id", lambda: "1")
    with pytest.raises(tg.TelegramUncertainError, match="2번째는 실패했습니다") as 오류:
        tg.send("리포트")
    assert "확실히 간 조각: 없음" in str(오류.value) and "그 뒤는 보내지 않았습니다" in str(오류.value)


def test_둘째도_모름이면_실패라_하지_않는다(monkeypatch) -> None:
    """UU 가 "그 뒤는 실패(…시간 초과)" 로 나왔다 (docs/infra.md 25.570, 교차검증)."""
    monkeypatch.setattr(tg, "_call", lambda *a, **k: (_ for _ in ()).throw(tg.TelegramUncertainError("초과")))
    monkeypatch.setattr(tg, "split_message", lambda text: ["가", "나", "다"])
    monkeypatch.setattr(tg, "resolve_chat_id", lambda: "1")
    with pytest.raises(tg.TelegramUncertainError, match="2번째도 보냈는지 모릅니다. 그 뒤는 보내지 않았습니다"):
        tg.send("리포트")
    # 마지막 조각이면 "그 뒤" 가 없다 (25.572)
    monkeypatch.setattr(tg, "split_message", lambda text: ["가", "나"])
    with pytest.raises(tg.TelegramUncertainError) as 오류:
        tg.send("리포트")
    assert "그 뒤는" not in str(오류.value)


class Test_200_인데_본문을_못_읽으면:
    """보낸 것으로 본다 — 실패로 세면 이미 간 리포트를 다음 트리거가 다시 보냈다 (docs/infra.md 25.754, 리포트 감사)."""

    def test_JSON_이_깨지면_실패다_25_757(self, 부르기) -> None:
        # requests 는 본문을 다 받은 뒤 돌아온다 — 깨진 JSON 은 텔레그램이 아닌 쪽(프록시 차단 HTML)이다 (25.757)
        부르기(가짜응답(200, ValueError("깨진 JSON")))
        with pytest.raises(tg.TelegramError):
            tg._call("sendMessage", {"chat_id": "1", "text": "x"})

    def test_result_가_없어도_보낸_것(self, 부르기) -> None:
        부르기(가짜응답(200, {"ok": True}))
        assert tg._call("sendMessage", {"chat_id": "1", "text": "x"})["result"] == {"message_id": None}

    def test_ok_false_는_여전히_실패(self, 부르기) -> None:
        부르기(가짜응답(200, {"ok": False, "description": "Bad Request"}))
        with pytest.raises(tg.TelegramError):
            tg._call("sendMessage", {"chat_id": "1", "text": "x"})
