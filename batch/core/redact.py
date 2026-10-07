"""오류 문구에서 비밀값을 가린다 (docs/infra.md 25.619·25.621).

requests 예외 문구는 요청 URL 을 그대로 담는다 — `…Max retries exceeded with url: /api/list.json?crtfc_key=<키>&…`.
DART 인증키는 쿼리(`crtfc_key`)로, 텔레그램 봇 토큰은 경로(`/bot<토큰>/`)로 가므로 둘 다 오류 문구에 실렸고, 그 문구가
경고 → 리포트(텔레그램 외부 발송)·`batch_runs.error_text`·Actions 로그(이슈 코멘트로 올라감)로 흘렀다.
여기에는 다른 모듈을 import 하지 않는다 — 어디서든 부를 수 있어야 한다.
"""

from __future__ import annotations

import re

#: 텔레그램 봇 토큰 — `bot123456789:AA…`
_봇토큰 = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
#: 쿼리의 키 값 — `crtfc_key=…`(DART) 와 흔한 이름들. 값은 `&`·공백·따옴표·괄호 전까지
_쿼리키 = re.compile(r"(?i)\b(crtfc_key|api_key|apikey|access_token|token|key)=([^&\s'\")]+)")


def 가림(text: str) -> str:
    """비밀값 꼴을 `***` 로 바꾼다. 가릴 것이 없으면 그대로."""
    text = _봇토큰.sub("bot***", text)
    return _쿼리키.sub(lambda m: f"{m.group(1)}=***", text)
