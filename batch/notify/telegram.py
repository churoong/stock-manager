"""텔레그램 발송.

무료 한도 수치가 공식 문서로 확인되지 않았다(docs/data-sources.md 12번).
그래서 알려진 숫자를 가정하지 않고, 429 응답이 돌려주는 retry_after 를
지키는 방식으로 구현한다. 실제 한도가 달라도 이 방식은 동작한다.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import requests

from batch import config
from batch.core.redact import 가림  # 정의는 한 곳이다 (25.621)

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"

# 텔레그램 텍스트 메시지 길이 상한으로 알려진 값 [확인필요]
# 공식 문서로 검증하지 않았으므로 여유를 두고 자른다.
MAX_LEN = 4096
SAFE_LEN = 3900

MAX_RETRY = 4
TIMEOUT = 20

#: 두 번 부르면 두 번 일어나는 호출. 요청이 건너간 뒤의 실패(읽기 시간 초과·보낸 뒤 끊김)에
#: 다시 부르지 않는다 — 연결 자체가 안 된 경우(`_보내기_전_실패`)만 다시 불러도 안전하다 (25.613).
NOT_IDEMPOTENT = frozenset({"sendMessage"})

#: 429 가 없거나 이상한 값을 줄 때 쓸 대기(초).
DEFAULT_RETRY_AFTER = 5

#: 429 가 **요구하는** 대기의 상한(초).
#:
#: 서버가 시키는 대로 자면 안 된다. GitHub Actions 는 **자는 동안에도 분을 센다** —
#: 이 파일은 한도가 공식 문서로 확인되지 않아(docs/data-sources.md 12번) retry_after 를
#: 그대로 따르게 짜여 있었는데, 텔레그램이 한 시간을 부르면 한 시간을 잤다. 네 번이면
#: 네 시간이고, 그것만으로 월 무료 분(2,000분)의 12% 다. 2026-09-20 에 무료 분이
#: 바닥났다(infra 25.27) — 이것이 원인이라는 증거는 없지만 `[확인필요]`, 이런 구멍을
#: 열어 둘 이유가 없다.
#:
#: 60 초를 넘겨 기다리라고 하면 **기다리지 않고 포기한다.** 리포트는 다음 실행이 다시
#: 보낸다. 알림 하나 늦는 것과 배치가 몇 시간 자는 것은 견줄 일이 아니다.
MAX_RETRY_AFTER = 60


class TelegramError(RuntimeError):
    pass


class TelegramPartialError(TelegramError):
    """여러 조각 가운데 **앞 조각은 이미 보냈고** 뒤 조각이 실패했다 (docs/infra.md 25.414).

    예전에는 보통의 실패와 같아서 보낸 조각의 message_id 가 버려졌다. 부르는 쪽은 "못 보냈다" 로 알고
    다음 실행에서 **리포트 전체를 다시** 보냈다 — 앞 조각을 두 번 받는다. 보낸 것을 들고 올린다.
    """

    def __init__(self, message: str, sent_ids: list[int], total: int) -> None:
        super().__init__(message)
        self.sent_ids = sent_ids
        self.total = total


class TelegramUncertainError(TelegramError):
    """응답 시간 초과로 **보냈는지 모른다** (docs/infra.md 25.493). 다시 보내면 두 번 갈 수 있다.

    예전에는 보통의 실패와 같아서, 첫 조각에서 이것이 나면 일일 배치가 실행을 `failed` 로 닫았다.
    그러면 같은 아침 뒤따르는 예비 트리거가 리포트 **전체를 다시** 보냈다 — 실제로 도착했던 리포트가 두 번 온다.
    부르는 쪽이 "모름" 으로 다룬다.
    """


def _보내기_전_실패(exc: requests.RequestException) -> bool:
    """요청이 **서버에 건너가기 전에** 난 실패인가 — 그때만 sendMessage 를 다시 보내도 안전하다 (25.613).

    연결 수립 시간 초과·연결 거부·이름 풀이 실패(urllib3 `NewConnectionError`)·TLS 악수 실패는 요청 본문이
    나가기 전이다. 요청을 보낸 **뒤** 연결이 끊기는 `ConnectionError`(RemoteDisconnected·Connection aborted)나
    `ChunkedEncodingError` 는 텔레그램이 이미 보냈을 수 있다.
    """
    from urllib3.exceptions import NewConnectionError

    # 프록시 실패(프록시 연결 거부·CONNECT 거절)는 텔레그램까지 가는 터널이 서기 전이다 (25.618, 교차검증 — 예전에는
    # "모름" 이 되어 프록시 환경의 평범한 일시 오류가 재시도를 잃었다)
    if isinstance(exc, (requests.ConnectTimeout, requests.exceptions.SSLError, requests.exceptions.ProxyError)):
        return True
    if isinstance(exc, requests.ConnectionError) and not isinstance(exc, requests.ReadTimeout):
        근 = exc.args[0] if exc.args else None
        return isinstance(getattr(근, "reason", 근), NewConnectionError)
    return False


def _token() -> str:
    return config.require("TELEGRAM_BOT_TOKEN")


def _call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    """한 번 호출한다. 429 면 retry_after 만큼 기다렸다 다시 시도한다."""
    url = f"{API_BASE}/bot{_token()}/{method}"

    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = requests.post(url, json=payload, timeout=TIMEOUT)
        except requests.RequestException as exc:
            if method in NOT_IDEMPOTENT and not _보내기_전_실패(exc):
                # 요청은 이미 건너갔다. 텔레그램이 보냈는지 알 수 없는데 다시 보내면
                # 같은 조각이 두 번 간다 (docs/infra.md 25.394). 읽기 시간 초과만이 아니라 **보낸 뒤 끊긴 연결**도
                # 같다 — 예전에는 그것을 다시 보냈다 (25.613, 감사: 로컬 서버가 읽고 끊자 두 번 받았다)
                raise TelegramUncertainError(
                    f"{method} 응답을 받지 못했습니다({type(exc).__name__}): 보냈는지 알 수 없어 다시 보내지 않습니다"
                    " (중복 방지)"
                ) from None  # 원래 예외는 URL(토큰)을 담는다 — 사슬로 traceback 에 찍히지 않게 (25.621)
            if attempt == MAX_RETRY:
                raise TelegramError(f"{method} 호출 실패: {가림(str(exc))}") from None
            wait = 2**attempt
            log.warning("텔레그램 %s 통신 오류, %d초 후 재시도", method, wait)
            time.sleep(wait)
            continue

        if resp.status_code == 429:
            wait = retry_after(_safe_json(resp))
            if wait > MAX_RETRY_AFTER:
                raise TelegramError(
                    f"{method} 호출 제한: {wait}초를 기다리라고 합니다."
                    f" {MAX_RETRY_AFTER}초가 넘으면 기다리지 않습니다 (Actions 분을 태웁니다)."
                    " 다음 실행에서 다시 보냅니다"
                )
            if attempt == MAX_RETRY:
                break  # 마지막 차례다. 자 봐야 다시 보내지 않는다
            log.warning("텔레그램 호출 제한. %d초 대기 후 재시도", wait)
            time.sleep(wait + 1)
            continue

        body = _safe_json(resp)
        if resp.status_code >= 500:
            if attempt == MAX_RETRY:
                raise TelegramError(f"{method} 서버 오류 {resp.status_code}")
            time.sleep(2**attempt)
            continue

        # **텔레그램이 ok:true 라고 했는데 result 만 없으면 보낸 것으로 본다** (docs/infra.md 25.754·25.757).
        # 25.754 는 "200 이면 본문을 못 읽어도 보낸 것" 이었는데, 파이썬 requests 는 본문을 끝까지 받은 뒤 돌아와
        # 끊긴 본문은 요청 예외(→ "보냈는지 모름")가 된다 — 그 분기에 오는 것은 **텔레그램이 아닌 쪽의 200**(가로채기
        # 프록시의 차단 HTML)뿐이라 안 보낸 리포트를 보낸 것으로 적었다(교차검증). JSON 을 못 읽으면 예전처럼 실패다
        if (
            method == "sendMessage"
            and resp.status_code == 200
            and body.get("ok") is True
            and not isinstance(body.get("result"), dict)
        ):
            log.warning("텔레그램 %s 200 인데 응답 본문을 읽지 못했습니다 — 보낸 것으로 봅니다", method)
            return {"ok": True, "result": {"message_id": None}}
        if not body.get("ok"):
            # description 에 토큰이 들어가지 않는다. 그대로 올려도 안전하다.
            raise TelegramError(
                f"{method} 실패 {resp.status_code}: {body.get('description', '응답 없음')}"
            )
        return body

    raise TelegramError(f"{method} 재시도 횟수를 모두 소진했습니다")


def retry_after(body: dict[str, Any]) -> int:
    """429 응답이 요구하는 대기(초). 이상한 값이면 기본값으로 돌아간다.

    바깥이 주는 값이라 모양을 믿지 않는다. `parameters` 가 dict 가 아니거나
    `retry_after` 가 null·글자·음수여도 여기서 터지면 안 된다 — **알림을 보내다가
    배치가 죽는다.**
    """
    params = body.get("parameters")
    raw = params.get("retry_after") if isinstance(params, dict) else None
    try:
        seconds = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER
    return seconds if seconds >= 0 else DEFAULT_RETRY_AFTER


def _safe_json(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _joined_len(lines: list[str]) -> int:
    return sum(len(x) for x in lines) + max(0, len(lines) - 1)


def split_message(text: str, limit: int = SAFE_LEN) -> list[str]:
    """길이 제한에 맞춰 나눈다. 줄 단위를 지키고, 들여 쓴 줄을 머리 줄과 떼지 않는다(25.819)."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.split("\n"):
        # 한 줄이 통째로 한도를 넘으면 강제로 쪼갠다
        while len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current, current_len = [], 0
            chunks.append(line[:limit])
            line = line[limit:]

        add = len(line) + (1 if current else 0)
        if current_len + add > limit:
            # **들여 쓴 줄은 머리 줄과 함께 간다** (docs/infra.md 25.819, 리포트 감사). 추천 한 건은 머리 줄 + 들여 쓴
            # 줄
            # (종가·팩터·매수 구간·근거)이라, 줄 단위로만 자르면 근거 문장이 다음 메시지로 떨어져 앞 조각에는 근거 없는
            # "종목 + 매수 구간" 만 남았다. 끊는 자리가 들여 쓴 줄이면 그 묶음의 머리 줄부터 넘긴다 — 묶음이 한도 안일
            # 때만
            cut = len(current)
            if line[:1] in (" ", "\t"):
                j = len(current) - 1
                while j > 0 and current[j][:1] in (" ", "\t"):
                    j -= 1
                if j > 0 and _joined_len(current[j:]) + 1 + len(line) <= limit:
                    cut = j
            chunks.append("\n".join(current[:cut]))
            current = current[cut:] + [line]
            current_len = _joined_len(current)
        else:
            current.append(line)
            current_len += add

    if current:
        chunks.append("\n".join(current))
    # **빈 조각은 보내지 않는다** (docs/infra.md 25.256). 빈 줄 다음에 한도만큼 긴 줄이 오면 빈 줄 하나만 든 조각이
    # 생겼다("a"*3900 + "\n\n" + "b"*3900 → [3900, 0, 3900]). 텔레그램은 빈 글을 거절하고, 웹 쪽은 앞 조각을 보낸 뒤라
    # 5분마다 그 앞 조각을 다시 보냈다. 경계의 빈 줄은 잃어도 뜻이 안 바뀐다
    return [c for c in chunks if c.strip()]


def resolve_chat_id() -> str:
    """설정된 `TELEGRAM_CHAT_ID` 를 쓴다. **없으면 보내지 않는다** (docs/infra.md 25.392).

    예전에는 비어 있으면 `getUpdates` 에서 **가장 최근에 봇에게 말을 건 대화방**을 골라 보냈다. 봇 이름은
    저장소에 적혀 있고(`step0_check`) Actions 는 시크릿이 없으면 빈 문자열을 넣으므로, 누군가 봇에게 말을 건 뒤
    배치가 돌면 **그 사람에게 리포트 전체(시세·보유)가 갔다** — 한국거래소 데이터의 제3자 제공 금지(CLAUDE.md) 위반이다.
    처음 설정할 때 대화방 번호를 찾는 일은 `discover_chat_ids()` 가 **보여 주기만** 한다.
    """
    configured = config.get("TELEGRAM_CHAT_ID")
    if configured:
        return configured
    후보 = discover_chat_ids()
    힌트 = f" 봇에게 말을 건 대화방: {', '.join(후보)} — 본인 것을 확인해 시크릿에 넣으세요" if 후보 else ""
    raise TelegramError(f"TELEGRAM_CHAT_ID 가 비어 있어 보내지 않습니다.{힌트}")


def discover_chat_ids() -> list[str]:
    """최근 봇에게 말을 건 대화방 번호들 — **설정을 돕는 용도로 보여 주기만 한다.** 여기로 보내지 않는다 (25.392)."""
    try:
        body = _call("getUpdates", {"limit": 10})
    except Exception:  # noqa: BLE001 — 찾지 못해도 설정 안내만 빠진다
        return []
    out: list[str] = []
    for update in reversed(body.get("result", [])):
        message = update.get("message") or update.get("channel_post") or {}
        chat_id = (message.get("chat") or {}).get("id")
        if chat_id is not None and str(chat_id) not in out:
            out.append(str(chat_id))
    return out


def append_disclaimer(text: str) -> str:
    """끝에 투자 책임 고지를 붙인다(이미 있으면 그대로). `send` 가 쓰는 규칙 한 곳이다.

    저장하는 쪽도 이것을 부른다 — **저장한 본문이 보낸 글과 같아야 한다** (docs/reports.md 1장, docs/infra.md 25.325).
    """
    # 텔레그램 고지는 2026-10-02 사용자 지시로 뺐다 (25.878) — 저장 본문도 이 함수를 거쳐 보낸 글과 같게 남는다
    if not config.TELEGRAM_DISCLAIMER:
        return text
    # **끝에 있는가** 를 본다 (25.613, 감사). 본문 중간(경고 인용 등)에 같은 문장이 있으면 예전에는 끝에 붙이지 않았다
    if text.rstrip().endswith(config.DISCLAIMER):
        return text
    return f"{text}\n\n{config.DISCLAIMER}"


def send(text: str, chat_id: str | None = None, with_disclaimer: bool = True) -> list[int]:
    """메시지를 보낸다. 보낸 message_id 목록을 돌려준다."""
    target = chat_id or resolve_chat_id()

    if with_disclaimer:
        text = append_disclaimer(text)

    message_ids: list[int] = []
    # 절 머리 다시 달기는 **보내는 쪽에서만** (25.1104) — `split_message` 는 웹(장중 알림 묶음)과 같은 표
    # (`tests/fixtures/telegram_split.json`)로 고정한 계약이고, 리포트는 파이썬만 보낸다
    chunks = _carry_headers(split_message(text))
    첫_조각_모름: TelegramUncertainError | None = None
    보낸_번호: list[int] = []
    for i, chunk in enumerate(chunks):
        try:
            body = _call(
                "sendMessage",
                {
                    "chat_id": target,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
            )
        except TelegramUncertainError as exc:
            # **첫 조각이 "모름" 이어도 나머지는 보낸다** (docs/infra.md 25.567, 감사). 예전에는 그대로 올려 2부·매도
            # 플래그·고지가 담긴 뒤 조각이 **확실히** 안 갔는데 "발송 여부 모름" 이라고만 했다. 뒤 조각은 한 번도
            # 보낸 적이 없어 다시 보내도 겹치지 않는다
            if i == 0 and len(chunks) > 1:
                첫_조각_모름 = exc
                continue
            if 첫_조각_모름 is not None:
                raise _모름으로(첫_조각_모름, message_ids, 보낸_번호, len(chunks), exc, i + 1) from exc
            if not message_ids:
                raise
            raise TelegramPartialError(
                f"{len(chunks)}조각 가운데 {len(message_ids)}조각만 보냈습니다: {가림(str(exc))}",
            message_ids, len(chunks),
            ) from exc
        except TelegramError as exc:
            # 첫 조각이 "모름" 이었으면 뒤 실패도 **"모름" 으로 올린다** (25.568, 교차검증) — 날것의 실패로 올려
            # 일일 배치가 `failed` 로 닫고 다음 트리거가 전체를 다시 보냈다.
            # 첫 조각이 실제로 갔다면 두 번 받는다(25.493 원칙)
            if 첫_조각_모름 is not None:
                raise _모름으로(첫_조각_모름, message_ids, 보낸_번호, len(chunks), exc, i + 1) from exc
            if not message_ids:
                raise
            raise TelegramPartialError(
                f"{len(chunks)}조각 가운데 {len(message_ids)}조각만 보냈습니다: {가림(str(exc))}",
            message_ids, len(chunks),
            ) from exc
        message_ids.append((body.get("result") or {}).get("message_id"))
        보낸_번호.append(i + 1)
    if 첫_조각_모름 is not None:
        raise _모름으로(첫_조각_모름, message_ids, 보낸_번호, len(chunks), None, 0) from 첫_조각_모름
    return message_ids


def _모름으로(
    첫: TelegramUncertainError, message_ids: list[int], 번호: list[int], total: int, 뒤: Exception | None,
    뒤_번호: int,
) -> TelegramUncertainError:
    """첫 조각이 "모름" 인 발송을 한 문장으로 (25.567·25.568·25.570). **어느 조각이 갔는지** 번호로 말한다.

    `sent_ids` 에 확실히 간 조각의 메시지 id 를 싣는다 — 일일 배치가 그것으로 발송 시각을 적는다(25.570).
    """
    # 마지막 조각이면 "그 뒤" 가 없다 (25.572, 교차검증)
    그뒤 = " 그 뒤는 보내지 않았습니다" if 뒤_번호 < total else ""
    if 뒤 is None:
        뒤말 = ""
    elif isinstance(뒤, TelegramUncertainError):
        뒤말 = f", {뒤_번호}번째도 보냈는지 모릅니다." + 그뒤
    else:
        뒤말 = f", {뒤_번호}번째는 실패했습니다({뒤})." + 그뒤
    간것 = ", ".join(f"{n}번째" for n in 번호) if 번호 else "없음"
    오류 = TelegramUncertainError(
        f"첫 조각(리포트 머리)은 보냈는지 모릅니다(응답 시간 초과). {total}조각 가운데 확실히 간 조각: {간것}{뒤말}"
        f" — 빠진 부분은 웹의 오늘 리포트에 있습니다: {첫}"
    )
    오류.sent_ids = list(message_ids)  # type: ignore[attr-defined]
    return 오류


#: 리포트의 절 머리 — 기간(`[단기]`)과 1부·2부 (docs/infra.md 25.1104)
_절_머리 = re.compile(r"^(\[(단기|중기|장기)\]|[12]부 .+)$")
_기간_머리 = re.compile(r"^\[(단기|중기|장기)\]( \(이어서\))?$")


def _carry_headers(chunks: list[str]) -> list[str]:
    """**절 머리를 조각마다 다시 단다** (docs/infra.md 25.1104, 리포트 감사 재현).

    25.819 는 들여 쓴 줄을 종목 머리와 묶었지만 `[장기]`·`2부 포트폴리오` 같은 절 머리는 들여 쓰지 않아, 첫 조각이
    `[장기]` 한 줄로 끝나고 둘째 조각은 기간 없이 종목으로 시작했다 — 매수 구간·근거가 기간마다 다른데 둘째 메시지만
    보면 어느 기간 신호인지 몰랐다. 조각 끝의 외톨이 절 머리는 다음 조각으로 넘기고, 절 머리 없이 시작하는 조각에는
    직전 **기간** 머리를 "(이어서)" 로 단다. 3,900자 안전 한도라 머리 한 줄은 텔레그램 한도(4,096) 안에 든다.
    """
    out: list[str] = []
    지금_머리: str | None = None
    넘김: list[str] = []
    for i, chunk in enumerate(chunks):
        줄들 = 넘김 + chunk.split("\n")
        넘김 = []
        while i < len(chunks) - 1 and len(줄들) > 1 and _절_머리.match(줄들[-1].strip()):
            넘김.insert(0, 줄들.pop())
        첫 = next((x for x in 줄들 if x.strip()), "")
        if out and 지금_머리 and not _절_머리.match(첫.strip()):
            줄들 = [f"{지금_머리} (이어서)", *줄들]
        for x in 줄들:
            # 이어 다는 것은 **기간 머리만** — 1부·2부 머리를 만나면 끊는다. 2부 뒤의 절(매도 플래그·일정)에
            # "[장기] (이어서)" 가 붙지 않게
            if _기간_머리.match(x.strip()):
                지금_머리 = x.strip().removesuffix(" (이어서)")
            elif _절_머리.match(x.strip()):
                지금_머리 = None
        out.append("\n".join(줄들))
    return out
