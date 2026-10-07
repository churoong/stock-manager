"""야후 파이낸스 펀드 프로필 어댑터 (미국 ETF).

보수·순자산·설정일·분류·상위 보유종목을 받는다. 2026-09-17 에 VOO·SPY·BND
등 미국 ETF 19개로 실제 응답 구조를 확인했다(docs/data-sources.md 13.3절).

왜 yfinance 를 거치지 않고 직접 부르나
  yfinance 의 `Ticker.info` 는 여러 모듈을 합쳐 키 이름을 바꿔 준다. 어떤 키로
  나오는지 이 PC 에서 확인할 방법이 없었다(파이썬이 없다). 대신 yfinance 가
  내부에서 부르는 quoteSummary 경로를 curl 로 직접 호출해 **원본 구조를
  눈으로 확인했다.** 확인한 구조를 그대로 읽는 쪽이 추측한 키를 읽는 것보다 낫다.

**접속은 curl_cffi 로 한다 (2026-09-17 실행 35166208076).**
  처음에는 requests 로 불렀다. PC 의 curl 에서는 됐는데 GitHub 러너에서는 crumb 요청부터
  HTTP 429 가 났다. 같은 러너에서 yfinance 일봉 25조각은 멀쩡히 받았다. 차이는
  접속 라이브러리다 — yfinance 는 curl_cffi 로 크롬의 TLS 지문을 흉내 내고, 야후는
  일반 파이썬 요청을 호출 제한으로 돌려보낸다고 알려져 있다 [확인필요: 공식 근거 없음].
  curl_cffi 는 yfinance 가 이미 끌어오는 의존성이라 새로 더하는 것이 없다.

인증 흐름 (yfinance 와 같다)
  1. fc.yahoo.com 에 접속해 쿠키를 받는다 (응답 코드는 404 여도 쿠키는 온다)
  2. 그 쿠키로 crumb 를 받는다
  3. quoteSummary 에 crumb 를 붙여 부른다
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

try:  # yfinance 의존성으로 설치된다. 없으면 requests 로 떨어진다(차단 가능성이 높다)
    from curl_cffi import requests as cffi_requests
except ImportError:  # pragma: no cover - 운영 환경에는 항상 있다
    cffi_requests = None

from batch import config
from batch.sources.yfinance_src import FetchResult, _is_rate_limit

log = logging.getLogger(__name__)

SOURCE = "yahoo_quotesummary"

COOKIE_URL = "https://fc.yahoo.com"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
MODULES = "fundProfile,defaultKeyStatistics,summaryDetail,topHoldings"

# curl_cffi 가 없을 때만 쓴다. curl_cffi 는 흉내 내는 브라우저의 값을 스스로 붙인다.
USER_AGENT = "Mozilla/5.0"
IMPERSONATE = "chrome"
TIMEOUT = 20

# 종목당 1회 호출이라 간격이 곧 총 소요다. 일괄 다운로드의 1.5초보다 짧게
# 두되, 차단 신호가 오면 물러선다. 공식 수치는 없다 [확인필요].
MIN_INTERVAL = 1.0
MAX_RETRY = 3

# 연속으로 이만큼 차단되면 멈춘다. 차단 뒤에 계속 부르면 더 오래 막힌다.
STOP_AFTER_BLOCKED = 3


@dataclass
class Holding:
    symbol: str
    name: str
    pct: float  # 0~1


@dataclass
class FundProfile:
    """ETF 한 개의 기본 정보. etf_profiles 한 행과 맞춘다."""

    symbol: str
    category: str | None = None
    family: str | None = None
    legal_type: str | None = None
    expense_ratio: float | None = None
    total_assets: float | None = None
    currency: str = "USD"
    inception_date: str | None = None
    avg_volume: float | None = None
    prev_close: float | None = None
    stock_position: float | None = None
    bond_position: float | None = None
    holdings: list[Holding] = field(default_factory=list)

    @property
    def turnover_est(self) -> float | None:
        """평균 거래량 × 전일 종가. 추정치다(yfinance_src.estimate_turnover 주석)."""
        if self.avg_volume is None or self.prev_close is None:
            return None
        return self.avg_volume * self.prev_close


def _raw(node: Any) -> Any:
    """야후는 수치를 {raw, fmt} 로 감싼다. 빈 객체 {} 는 값이 없다는 뜻이다."""
    if isinstance(node, dict):
        return node.get("raw")
    return node


def _fmt(node: Any) -> Any:
    if isinstance(node, dict):
        return node.get("fmt")
    return node


def parse_quote_summary(symbol: str, payload: dict) -> FundProfile | None:
    """quoteSummary 응답을 FundProfile 로. 결과가 없으면 None.

    값이 없는 항목은 None 으로 둔다. 0 으로 채우지 않는다. 0% 보수와 "보수를
    모른다" 는 전혀 다른 말이다.
    """
    result = (payload.get("quoteSummary") or {}).get("result") or []
    if not result:
        return None
    r = result[0] or {}

    fp = r.get("fundProfile") or {}
    ks = r.get("defaultKeyStatistics") or {}
    sd = r.get("summaryDetail") or {}
    th = r.get("topHoldings") or {}

    fees = fp.get("feesExpensesInvestment") or {}

    # 순자산은 summaryDetail 에 있고, 없으면 defaultKeyStatistics 에서 찾는다.
    # 2026-09-17 확인한 응답에는 두 곳 모두 같은 값이 있었다. 한쪽이 비는 경우에
    # 대비한 것이지 실제로 본 경우는 아니다 [확인필요].
    total_assets = _raw(sd.get("totalAssets"))
    if total_assets is None:
        total_assets = _raw(ks.get("totalAssets"))

    # **비중을 모르는 보유는 빼고 0 으로 채우지 않는다** (docs/infra.md 25.391, 위 원칙과 같다). 예전에는 `or 0.0` 이라
    # 비중 없는 종목이 "0.00%" 로 겹침에 잡혀 "추천 종목 NVDA 0.00%" 처럼 **모르는 것을 0 으로** 보였다
    holdings = [
        Holding(
            symbol=str(h.get("symbol") or ""),
            name=str(h.get("holdingName") or ""),
            pct=float(_raw(h.get("holdingPercent"))),
        )
        for h in (th.get("holdings") or [])
        if h.get("symbol") and isinstance(_raw(h.get("holdingPercent")), (int, float))
    ]

    def _float(value: Any) -> float | None:
        return None if value is None else float(value)

    return FundProfile(
        symbol=symbol,
        category=fp.get("categoryName") or None,
        family=fp.get("family") or None,
        legal_type=fp.get("legalType") or None,
        expense_ratio=_float(_raw(fees.get("annualReportExpenseRatio"))),
        total_assets=_float(total_assets),
        currency=str(sd.get("currency") or "USD"),
        inception_date=_fmt(ks.get("fundInceptionDate")) or None,
        avg_volume=_float(_raw(sd.get("averageVolume"))),
        prev_close=_float(_raw(sd.get("previousClose"))),
        stock_position=_float(_raw(th.get("stockPosition"))),
        bond_position=_float(_raw(th.get("bondPosition"))),
        holdings=holdings,
    )


class YahooFundClient:
    """세션 하나로 여러 종목을 부른다. 쿠키와 crumb 를 한 번만 받는다."""

    def __init__(self) -> None:
        if cffi_requests is not None:
            self.session: Any = cffi_requests.Session(impersonate=IMPERSONATE)
        else:
            self.session = requests.Session()
            self.session.headers["User-Agent"] = USER_AGENT
        self.crumb: str | None = None
        self.blocked_in_a_row = 0

    def _ensure_crumb(self) -> str:
        if self.crumb:
            return self.crumb
        # 쿠키 요청은 404·연결 종료로 끝나기도 한다. 쿠키만 받으면 된다.
        # 라이브러리마다 예외 종류가 달라 모두 삼킨다.
        with contextlib.suppress(Exception):
            self.session.get(COOKIE_URL, timeout=TIMEOUT)
        response = self.session.get(CRUMB_URL, timeout=TIMEOUT)
        crumb = response.text.strip()
        if response.status_code != 200 or not crumb or "<" in crumb:
            raise RuntimeError(f"crumb 를 받지 못했습니다 (HTTP {response.status_code})")
        self.crumb = crumb
        return crumb

    def fetch(self, symbol: str) -> FetchResult:
        """한 종목. 실패해도 예외 대신 결과로 돌려준다."""
        if self.blocked_in_a_row >= STOP_AFTER_BLOCKED:
            return FetchResult(ok=False, source=SOURCE, limit_state="blocked", error="차단으로 중단")

        last_error = ""
        for attempt in range(1, MAX_RETRY + 1):
            try:
                crumb = self._ensure_crumb()
                response = self.session.get(
                    SUMMARY_URL.format(symbol=symbol),
                    params={"modules": MODULES, "crumb": crumb},
                    timeout=TIMEOUT,
                )
            except Exception as exc:  # requests·curl_cffi 예외와 crumb 실패를 모두 받는다
                last_error = str(exc)
                time.sleep(2 * attempt)
                continue

            if response.status_code == 429:
                self.blocked_in_a_row += 1
                last_error = "HTTP 429"
                log.warning("야후 호출 제한 (%s, 연속 %d회)", symbol, self.blocked_in_a_row)
                if self.blocked_in_a_row >= STOP_AFTER_BLOCKED:
                    return FetchResult(ok=False, source=SOURCE, limit_state="blocked", error=last_error)
                time.sleep(10 * attempt)
                continue

            if response.status_code == 401:
                # crumb 가 만료됐다. 한 번 다시 받는다.
                self.crumb = None
                last_error = "HTTP 401"
                continue

            if response.status_code == 404:
                return FetchResult(ok=False, source=SOURCE, limit_state="ok", error="HTTP 404 종목 없음")

            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
                time.sleep(2 * attempt)
                continue

            self.blocked_in_a_row = 0
            try:
                profile = parse_quote_summary(symbol, response.json())
            except (ValueError, TypeError) as exc:
                return FetchResult(ok=False, source=SOURCE, limit_state="ok", error=f"응답 해석 실패: {exc}")
            if profile is None:
                return FetchResult(ok=False, source=SOURCE, limit_state="ok", error="결과 없음")
            return FetchResult(
                ok=True, source=SOURCE, data=profile, limit_state="ok", fetched_at=datetime.now(UTC)
            )

        state = "blocked" if _is_rate_limit(Exception(last_error)) else "unknown"
        return FetchResult(ok=False, source=SOURCE, limit_state=state, error=last_error)


def fetch_profiles(symbols: list[str]) -> tuple[dict[str, FundProfile], list[str], bool]:
    """여러 종목의 프로필. (받은 것, 실패 메모, 차단으로 멈췄는가)."""
    if config.SETTINGS.offline_mode:
        return _offline(symbols), [], False

    client = YahooFundClient()
    profiles: dict[str, FundProfile] = {}
    failures: list[str] = []

    for index, symbol in enumerate(symbols):
        if index:
            time.sleep(MIN_INTERVAL)
        result = client.fetch(symbol)
        if result.ok:
            profiles[symbol] = result.data
        else:
            failures.append(f"{symbol}: {result.error}")
            if result.limit_state == "blocked":
                log.warning("차단으로 남은 %d종목을 건너뜁니다", len(symbols) - index - 1)
                return profiles, failures, True
        if (index + 1) % 100 == 0:
            log.info("프로필 %d/%d", index + 1, len(symbols))

    return profiles, failures, False


def _offline(symbols: list[str]) -> dict[str, FundProfile]:
    fixture = Path(config.ROOT) / "tests" / "fixtures" / "us_fund_profiles.json"
    if not fixture.exists():
        return {}
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    out: dict[str, FundProfile] = {}
    for symbol in symbols:
        payload = raw.get(symbol)
        if payload:
            profile = parse_quote_summary(symbol, payload)
            if profile:
                out[symbol] = profile
    return out
