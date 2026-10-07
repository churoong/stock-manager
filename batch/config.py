"""환경변수 로딩과 실행 설정.

로컬에서는 .env 파일을 읽고, GitHub Actions 와 Vercel 에서는
플랫폼이 넣어 준 환경변수를 그대로 쓴다. 코드에 키를 적지 않는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv_if_present() -> None:
    """로컬 개발용. .env 가 있으면 읽고, 없으면 조용히 넘어간다."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        # python-dotenv 가 없어도 클라우드에서는 문제없다
        return
    load_dotenv(env_path, override=False)


_load_dotenv_if_present()


def get(name: str, default: str = "") -> str:
    """환경변수를 읽는다. 앞뒤 공백을 떼어 낸다."""
    return os.environ.get(name, default).strip()


def get_bool(name: str, default: bool = False) -> bool:
    raw = get(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def require(name: str) -> str:
    """없으면 바로 실패한다. 값은 절대 로그에 남기지 않는다."""
    value = get(name)
    if not value:
        raise RuntimeError(
            f"환경변수 {name} 이(가) 비어 있습니다. "
            f".env 또는 Actions 시크릿을 확인하세요."
        )
    return value


def mask(value: str) -> str:
    """로그에 찍어도 되는 형태로 바꾼다. 길이만 드러낸다."""
    if not value:
        return "(비어있음)"
    return f"(설정됨, {len(value)}자)"


@dataclass(frozen=True)
class Settings:
    offline_mode: bool
    log_level: str
    continue_on_item_error: bool

    @classmethod
    def load(cls) -> Settings:
        return cls(
            offline_mode=get_bool("OFFLINE_MODE", False),
            log_level=get("LOG_LEVEL", "INFO").upper(),
            continue_on_item_error=get_bool("CONTINUE_ON_ITEM_ERROR", True),
        )


SETTINGS = Settings.load()

#: 웹앱 주소 (예: https://stock.example.vercel.app). 텔레그램 리포트의 종목 줄에 **매매 입력 딥링크**를 붙이는 데 쓴다
#: (docs/infra.md 25.944). 비어 있으면 링크를 붙이지 않는다 — 리포트의 다른 내용은 그대로다.
#: 비밀이 아니라 Actions 변수(`vars.APP_URL`)다
APP_URL = get("APP_URL").rstrip("/")

# 투자 책임 고지. 모든 텔레그램 메시지 끝에 붙인다.
DISCLAIMER = "투자 판단의 책임은 본인에게 있습니다."
#: 텔레그램 메시지 끝에 고지를 붙이나 — **붙이지 않는다** (2026-10-02 사용자 지시: "텔레그램 메시지에 '투자 판단의
#: 책임은 본인에게
#: 있습니다.' 이거 다 빼", docs/infra.md 25.878). 웹 화면 하단 고지는 그대로다(CLAUDE.md 절대 규칙)
TELEGRAM_DISCLAIMER = False

# FRED 데이터를 쓰는 화면·메시지에 넣어야 하는 고지 (FRED 이용약관 의무)
FRED_NOTICE = (
    "This product uses the FRED® API but is not endorsed or certified "
    "by the Federal Reserve Bank of St. Louis."
)
