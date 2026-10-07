"""DART 임원·주요주주 특정증권등 소유상황보고 (elestock.json, docs/data-sources.md 16.1).

2026-09-18 실측 (삼성전자 00126380, Actions 35285934191):
  회사 하나씩 부른다. **기간 파라미터가 없다** — `bgn_de`/`end_de` 를 줘도 행 수가 그대로였다.
  응답은 **최근 약 2년치**(실측 2024-09-19 ~ 2026-09-17, 3,400행, 1.1MB). 기간은 받아서 거른다.
  증감은 부호가 붙은 문자열("-500"), 콤마가 들어간다. 0 인 행과 숫자 자리에 "-" 인 행이 있다.
  접수번호는 한 건에 한 행이었다(3,400종 / 3,400행).

**사유 코드가 없다.** 장내 매수와 증여·상속·스톡옵션 행사를 구분할 수 없다. 그래서 증감 부호만으로
buy/sell 을 정하고, 이 값은 판정에 쓰지 않고 근거표의 참고 행으로만 보여 준다 (docs/signals.md 8장).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import requests

from batch import config
from batch.core.redact import 가림  # 예외 문구의 URL 에 인증키가 있다 (25.621)
from batch.sources.dart import BASE_URL, SOURCE, STATUS_DAILY_LIMIT, STATUS_NO_DATA, STATUS_OK, TIMEOUT
from batch.sources.yfinance_src import FetchResult

log = logging.getLogger(__name__)

ENDPOINT = f"{BASE_URL}/elestock.json"
_NUMBER = re.compile(r"^-?[0-9,]+$")


@dataclass(frozen=True)
class InsiderReport:
    """보고서 한 건. 원문 문구는 옮기지 않고 저장에 필요한 값만 둔다."""

    receipt_no: str
    filed_date: str  # YYYY-MM-DD
    insider: str
    role: str | None
    shares_delta: int  # 부호 있음. 저장할 때 action 과 절댓값으로 나눈다
    shares_after: int | None

    @property
    def action(self) -> str:
        """증감 부호만으로 정한다. 사유 코드가 없어 증여·행사도 여기 섞인다(문서 16.1)."""
        if self.shares_delta > 0:
            return "buy"
        return "sell" if self.shares_delta < 0 else "other"


def parse_number(text: Any) -> int | None:
    """콤마·부호가 든 문자열을 정수로. 빈 값이나 "-" 같은 자리표시는 None."""
    value = str(text or "").strip()
    if not _NUMBER.match(value):
        return None
    return int(value.replace(",", ""))


def parse_role(row: dict) -> str | None:
    """직위·관계를 한 줄로. 등기 여부와 주요주주 여부는 뜻이 달라 함께 남긴다."""
    parts = [str(row.get(key) or "").strip() for key in ("isu_exctv_rgist_at", "isu_exctv_ofcps")]
    major = str(row.get("isu_main_shrholdr") or "").strip()
    if major and major != "-":
        parts.append(major)
    joined = " ".join(p for p in parts if p)
    return joined or None


def parse_reports(body: dict, since: str | None = None) -> tuple[list[InsiderReport], int]:
    """응답 → (보고서 목록, 읽지 못해 버린 행 수). since 이전 접수일은 버리지 않고 거른다(센지 않음)."""
    reports: list[InsiderReport] = []
    dropped = 0
    for row in body.get("list") or []:
        filed = str(row.get("rcept_dt") or "").strip()
        receipt = str(row.get("rcept_no") or "").strip()
        insider = str(row.get("repror") or "").strip()
        delta = parse_number(row.get("sp_stock_lmp_irds_cnt"))
        if not filed or not receipt or not insider or delta is None:
            dropped += 1
            continue
        if since and filed < since:
            continue
        reports.append(
            InsiderReport(
                receipt_no=receipt,
                filed_date=filed,
                insider=insider,
                role=parse_role(row),
                shares_delta=delta,
                shares_after=parse_number(row.get("sp_stock_lmp_cnt")),
            )
        )
    return reports, dropped


def fetch_reports(corp_code: str, since: str | None = None) -> FetchResult:
    """회사 하나의 보고 목록. data 는 (보고서 목록, 버린 행 수)."""
    if config.SETTINGS.offline_mode:
        return FetchResult(ok=False, source=SOURCE, error="오프라인 모드", attempts=0)
    key = config.get("DART_API_KEY")
    if not key:
        # 부르지 않았으면 세지 않는다 (25.605) — 예전에는 키가 비어도 회사마다 1회씩 한도 카운터가 올랐다
        return FetchResult(ok=False, source=SOURCE, error="DART_API_KEY 가 비어 있습니다", attempts=0)
    try:
        response = requests.get(
            ENDPOINT, params={"crtfc_key": key, "corp_code": corp_code}, timeout=TIMEOUT
        )
    except requests.RequestException as exc:
        return FetchResult(ok=False, source=SOURCE, error=f"호출 실패: {가림(str(exc))}")
    if response.status_code != 200:
        return FetchResult(ok=False, source=SOURCE, error=f"HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        return FetchResult(ok=False, source=SOURCE, error="응답을 해석하지 못했습니다")

    status = str(body.get("status", ""))
    if status == STATUS_NO_DATA:  # 보고가 없는 회사. 오류가 아니다
        return FetchResult(ok=True, source=SOURCE, data=([], 0), limit_state="ok")
    if status != STATUS_OK:
        return FetchResult(
            ok=False, source=SOURCE, error=f"DART 오류 {status}: {body.get('message', '')}",
            limit_state="blocked" if status == STATUS_DAILY_LIMIT else "unknown",
        )  # fmt: skip
    return FetchResult(ok=True, source=SOURCE, data=parse_reports(body, since), limit_state="ok")
