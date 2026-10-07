"""야후 파이낸스 어댑터.

공식 API 가 아니라 호출 제한 수치가 공개돼 있지 않다(docs/data-sources.md 4번).
제한은 IP 기준이고 GitHub 러너는 공용 데이터센터 대역을 쓰기 때문에,
클라우드에서 429 가 날 위험이 있다. 이것이 Step 0 의 핵심 검증 항목이다.

완화책은 세 가지다.
  1. 종목별 반복 호출 대신 일괄 다운로드로 요청 수를 줄인다
  2. 호출 사이에 간격을 두고, 429 면 물러섰다 재시도한다
  3. 실패해도 배치가 죽지 않게 예외 대신 결과 객체로 돌려준다
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from batch import config

log = logging.getLogger(__name__)

SOURCE = "yfinance"

# 호출 사이 최소 간격(초). 공식 수치가 없으므로 보수적으로 둔다.
MIN_INTERVAL = 1.5
MAX_RETRY = 3


@dataclass
class FetchResult:
    """어댑터 공통 반환 형태.

    배치가 한 소스의 실패로 통째로 멈추지 않도록 예외 대신 이것을 쓴다.
    """

    ok: bool
    data: Any = None
    source: str = SOURCE
    fetched_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )
    from_cache: bool = False
    limit_state: str = "unknown"  # ok | warn | blocked | unknown
    error: str = ""
    #: 실제로 원격에 보낸 요청 수 — 재시도 포함 (docs/infra.md 25.390). 부르는 쪽이 이 수로 한도 카운터를 센다.
    #: 예전에는 늘 1 로 세서 5xx 두 번 뒤 성공한 KRX 호출(서버엔 3번)이 카운터에 1로만 잡혔다. 보내지 않았으면 0
    attempts: int = 1

    @property
    def rate_limited(self) -> bool:
        return self.limit_state == "blocked"


def _is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "ratelimit" in text or "429" in text or "too many requests" in text


@dataclass
class DailyQuote:
    """하루치 종가 정보. 화면과 메시지가 공통으로 쓰는 최소 단위."""

    ticker: str
    trade_date: str
    close: float
    prev_close: float | None
    volume: int | None
    currency: str

    @property
    def change_pct(self) -> float | None:
        if self.prev_close in (None, 0):
            return None
        return (self.close - self.prev_close) / self.prev_close * 100


def _download(tickers: list[str], lookback_days: int) -> tuple[Any, str, str]:
    """일괄 다운로드 한 번. 재시도까지 여기서 끝낸다.

    돌려주는 것은 (프레임, 오류 문구, 한도 상태) 세 쪽이다. 프레임이 None 이면
    실패다. 한도 상태가 blocked 면 더 부르지 말아야 한다는 뜻이다.

    이 함수가 따로 있는 이유는 최근 종가만 쓰는 경로와 여러 날을 쓰는 경로가
    같은 재시도 규칙을 써야 하기 때문이다. 규칙이 두 벌이면 한쪽만 고치게 된다.
    """
    try:
        import yfinance as yf
    except ImportError as exc:
        return None, f"yfinance 를 불러오지 못했습니다: {exc}", "unknown"

    last_error = ""
    for attempt in range(1, MAX_RETRY + 1):
        로그 = _로그모음()
        _yf로거 = logging.getLogger("yfinance")
        _yf로거.addHandler(로그)
        try:
            frame = yf.download(
                tickers=" ".join(tickers),
                period=f"{lookback_days}d",
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,  # 동시 요청은 차단 위험을 키운다
                group_by="ticker",
            )
        except Exception as exc:  # noqa: BLE001 - 어떤 실패든 결과로 감싼다
            _yf로거.removeHandler(로그)
            last_error = str(exc)
            if _is_rate_limit(exc):
                log.warning("야후 호출 제한으로 보입니다 (시도 %d)", attempt)
                if attempt == MAX_RETRY:
                    return None, last_error, "blocked"
                time.sleep(5 * attempt)
                continue
            if attempt == MAX_RETRY:
                return None, last_error, "unknown"
            time.sleep(2 * attempt)
            continue

        # **yf.download 는 종목별 예외(429 포함)를 삼킨다** (docs/infra.md 25.601·25.603·25.605). 1.x 는 종목마다 따로
        # 요청하고 실패를 호출마다 만드는 오류 목록에 넣은 채 빈 표를 돌려준다 — 그 목록은 밖으로 안 나온다
        # (`yf.shared._ERRORS` 는 1.7.0 에서 죽은 코드다). 대신 yfinance 가 남기는 **로그**를 모아 막힌 종목 수를 센다
        # [확인필요: 판마다 문구가 다를 수 있다 — 못 잡으면 조각 단위 판정(빈 조각 두 번)이 받친다]
        _yf로거.removeHandler(로그)
        막힘 = [m for m in 로그.messages if "rate limit" in m.lower() or "too many requests" in m.lower()]
        막힌수 = sum(_로그_종목수(m) for m in 막힘)
        if 막힘 and 막힌수 * 2 >= len(tickers):
            # **받은 표는 버리지 않는다** (25.606, 교차검증 — 25.386 원칙). 예전에는 None 을 돌려줘 절반만 막힌 조각의
            # 멀쩡한 종목까지 버렸다. 부르는 쪽이 표를 살리고 멈춘다
            return frame, f"야후 호출 제한: {막힌수}/{len(tickers)}종목 ({막힘[0][:80]})", "blocked"
        return frame, "", "ok"

    return None, last_error or "알 수 없는 실패", "unknown"


def _로그_종목수(message: str) -> int:
    """yfinance 오류 로그 한 줄이 가리키는 종목 수.

    1.7.0 은 **같은 문구의 오류를 한 줄로 묶어** `['AAA', 'BBB']: YFRateLimitError(...)` 로 찍는다(multi.py).
    줄 수로 세면 200종목 조각이 모두 막혀도 1건이라 판정이 한 번도 서지 않았다 (docs/infra.md 25.605, 교차검증).
    목록이 없는 줄은 1종목으로 본다.
    """
    묶음 = re.match(r"\s*\[([^\]]*)\]", message)
    if not 묶음:
        return 1
    return max(1, len(re.findall(r"'[^']*'|\"[^\"]*\"", 묶음.group(1))))


class _로그모음(logging.Handler):
    """yfinance 로그를 모은다 — 삼킨 종목별 오류를 보는 유일한 창이다 (25.603)."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):
            self.messages.append(record.getMessage())


def fetch_daily_quotes(tickers: list[str], lookback_days: int = 10) -> FetchResult:
    """여러 종목의 최근 종가를 한 번에 받는다.

    종목마다 따로 부르지 않는다. 요청 수가 곧 차단 위험이기 때문이다.
    """
    if config.SETTINGS.offline_mode:
        return _offline_quotes(tickers)

    frame, error, limit_state = _download(tickers, lookback_days)
    if frame is None:
        return FetchResult(ok=False, limit_state=limit_state, error=error)

    quotes = _to_quotes(frame, tickers)
    if limit_state == "blocked":  # 로그로 본 호출 제한 — 받은 것은 담고 막혔다고 말한다 (25.606)
        return FetchResult(ok=bool(quotes), data=quotes, limit_state="blocked", error=error)
    if not quotes:
        return FetchResult(
            ok=False,
            error="응답은 왔으나 사용할 수 있는 행이 없습니다",
            limit_state="warn",
        )
    return FetchResult(ok=True, data=quotes, limit_state="ok")


def _to_quotes(frame: Any, tickers: list[str]) -> list[DailyQuote]:
    """다운로드 결과를 DailyQuote 목록으로 바꾼다.

    종목이 하나일 때와 여럿일 때 열 구조가 달라서 나눠 처리한다.
    """
    quotes: list[DailyQuote] = []
    if frame is None or len(frame) == 0:
        return quotes

    for ticker in tickers:
        try:
            if len(tickers) == 1 and ticker not in getattr(
                frame.columns, "levels", [[]]
            )[0]:
                closes = frame["Close"].dropna()
                volumes = frame["Volume"].dropna()
            else:
                closes = frame[ticker]["Close"].dropna()
                volumes = frame[ticker]["Volume"].dropna()
        except (KeyError, IndexError, TypeError):
            log.warning("%s 의 열을 찾지 못했습니다", ticker)
            continue

        if len(closes) == 0:
            log.warning("%s 의 종가가 비어 있습니다", ticker)
            continue

        last_date = closes.index[-1]
        prev_close = float(closes.iloc[-2]) if len(closes) >= 2 else None
        volume = int(volumes.iloc[-1]) if len(volumes) else None

        quotes.append(
            DailyQuote(
                ticker=ticker,
                trade_date=last_date.strftime("%Y-%m-%d"),
                close=float(closes.iloc[-1]),
                prev_close=prev_close,
                volume=volume,
                currency=_guess_currency(ticker),
            )
        )
    return quotes


def _guess_currency(ticker: str) -> str:
    """접미사로 통화를 판단한다. 한국 종목은 .KS 또는 .KQ 를 쓴다."""
    upper = ticker.upper()
    if upper.endswith(".KS") or upper.endswith(".KQ"):
        return "KRW"
    return "USD"


def _offline_quotes(tickers: list[str]) -> FetchResult:
    """오프라인 모드. 네트워크를 타지 않고 고정 데이터를 돌려준다."""
    import json
    from pathlib import Path

    fixture = Path(config.ROOT) / "tests" / "fixtures" / "daily_quotes.json"
    if not fixture.exists():
        return FetchResult(ok=False, error=f"픽스처가 없습니다: {fixture}")

    raw = json.loads(fixture.read_text(encoding="utf-8"))
    quotes = [
        DailyQuote(**row) for row in raw.get("quotes", []) if row["ticker"] in tickers
    ]
    return FetchResult(ok=True, data=quotes, limit_state="ok", from_cache=True)


# ----------------------------------------------------------------------
# 일봉 대량 수집 (미국 전종목)
# ----------------------------------------------------------------------
#
# fetch_daily_quotes 는 "최근 종가 한 줄"만 돌려준다. 리포트에는 그것으로
# 충분하지만 유니버스 판정에는 모자란다. 20일 평균 거래대금을 내려면
# 거래일이 20개 쌓여야 하고, 성과 지표는 그보다 훨씬 많이 필요하다.
#
# 그래서 받은 창 안의 모든 거래일을 돌려주는 경로를 따로 둔다. 같은 요청으로
# 여러 날을 받아 두면 하루 빠진 날이 다음 실행에서 저절로 메워진다.

# 한 요청에 묶을 심볼 수.
#
# 미국은 종목이 수천 개라 한 번에 다 보낼 수 없다. 나눠 보내되 **조각 수가 곧
# 요청 수**이고 요청 수가 곧 시간이자 차단 위험이므로 잘게 쪼개지 않는다.
#
# 2026-09-16 운영 실측(docs/infra.md 12절)
#   5,684종목을 200개씩 29조각 → 16분 40초, 조각당 약 34초, 429 는 0건
#
# **시간의 대부분이 조각 수에서 온다.** 창(lookback)을 며칠로 줄여도 요청 수가
# 같아 시간은 줄지 않는다. 줄이려면 이 값을 키워야 한다. 다만 야후는 한 요청에
# 담을 수 있는 심볼 수의 상한을 공개하지 않는다 [확인필요 — 공식 수치 부재].
#
# 그래서 기본값은 **실측으로 확인된 200 을 그대로 둔다.** 검증되지 않은 값을
# 기본값으로 삼지 않는다는 규칙 그대로다. 대신 환경변수로 바꿀 수 있게 해서,
# 코드를 고치지 않고 한 번 재 보고 결정할 수 있게 했다.
#
#   YF_CHUNK_SYMBOLS=500 python -m batch.jobs.daily --market US --force --dry-run
#
# 실측해서 500 이 안전하다고 확인되면 그때 이 기본값을 올린다.
CHUNK_SYMBOLS = int(config.get("YF_CHUNK_SYMBOLS", "") or 200)


@dataclass
class DailyBar:
    """하루치 일봉. prices 테이블의 한 행과 1:1로 맞춘다."""

    ticker: str
    date: str
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    adj_close: float | None = None
    volume: int | None = None
    currency: str = "USD"


def chunk_count(total: int, chunk_size: int = CHUNK_SYMBOLS) -> int:
    """심볼 수를 요청 수로 바꾼다. 한도 카운터가 이 값을 센다.

    호출 수를 사람이 어림해 적어 두면 조각 크기를 바꿀 때 같이 고치는 것을
    잊는다. 실제로 재무 배치에서 열 개수를 그렇게 놓쳐 운영에서 죽었다.
    """
    if chunk_size < 1:
        raise ValueError("chunk_size 는 1 이상이어야 합니다")
    if total <= 0:
        return 0
    return (total + chunk_size - 1) // chunk_size


def estimate_turnover(close: float | None, volume: int | None) -> int | None:
    """거래대금 추정치. 종가 × 거래량.

    **야후는 거래대금을 주지 않는다.** 한국거래소는 실제 거래대금을 주지만
    야후 응답에는 그 항목이 없다. 유니버스의 거래대금 하한을 미국에도
    적용하려면 무언가로 갈음해야 해서 종가와 거래량을 곱한다.

    이것은 추정치다. 실제 거래대금은 체결가마다 다른 가격으로 쌓이므로
    종가 하나를 곱한 값과 같을 수 없고, 장중 변동이 큰 날일수록 벌어진다.

    그래도 쓰는 이유는 유니버스 판정이 **자릿수를 가르는 일**이기 때문이다.
    하루 100만 달러와 1억 달러를 구분하는 데는 이 정밀도로 충분하다.
    반대로 거래대금 자체를 화면이나 리포트에 수치로 싣는다면, 그때는
    추정치임을 반드시 함께 표시해야 한다.
    """
    if close is None or volume is None:
        return None
    return int(round(close * volume))


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def fetch_daily_bars(
    symbols: list[str],
    lookback_days: int = 10,
    chunk_size: int = CHUNK_SYMBOLS,
) -> FetchResult:
    """여러 종목의 최근 며칠치 일봉을 받는다.

    심볼을 조각으로 나눠 요청하고, 조각 사이에 간격을 둔다. 한 조각이
    실패해도 나머지는 살린다. 일부라도 받았으면 ok 로 돌려주고 무엇을
    놓쳤는지는 error 에 적는다. 전부 실패했을 때만 ok 가 False 다.

    부분 실패를 성공으로 감싸는 이유는, 수천 종목 중 몇 개가 빠졌다고
    그날 미국 배치를 통째로 버리는 것이 손해이기 때문이다. 대신 빠진
    사실이 조용히 묻히지 않도록 호출한 쪽에 반드시 문자열로 전달한다.
    """
    if chunk_size < 1:
        raise ValueError("chunk_size 는 1 이상이어야 합니다")
    if not symbols:
        return FetchResult(ok=False, error="대상 심볼이 없습니다")

    if config.SETTINGS.offline_mode:
        return _offline_bars(symbols)

    bars: list[DailyBar] = []
    missing: list[str] = []
    failed_chunks = 0
    빈조각 = 0
    blocked = False
    last_error = ""

    groups = _chunks(symbols, chunk_size)
    log.info(
        "일봉 수집 시작: %d종목을 %d개씩 %d조각",
        len(symbols),
        chunk_size,
        len(groups),
    )

    for index, group in enumerate(groups):
        if index:
            time.sleep(MIN_INTERVAL)

        # 조각당 소요를 남긴다. 조각 크기를 바꿀지 판단하는 근거가 된다.
        started = time.monotonic()
        frame, error, limit_state = _download(group, lookback_days)
        log.info(
            "조각 %d/%d %.1f초%s",
            index + 1,
            len(groups),
            time.monotonic() - started,
            "" if frame is not None else f" 실패: {error}",
        )
        if frame is None:
            failed_chunks += 1
            last_error = error
            if limit_state == "blocked":
                # 차단당한 뒤에 더 부르면 더 오래 막힌다. 여기서 멈춘다.
                blocked = True
                log.warning("야후 차단으로 남은 조각을 건너뜁니다 (%d/%d)", index + 1, len(groups))
                break
            continue

        got, lost = _to_bars(frame, group)
        bars.extend(got)
        missing.extend(lost)
        if limit_state == "blocked":
            # 로그로 본 호출 제한 — 이 조각에서 받은 것은 담고 멈춘다 (25.606, 교차검증)
            failed_chunks += 1
            last_error = error
            blocked = True
            log.warning("야후 호출 제한으로 남은 조각을 건너뜁니다 (%d/%d)", index + 1, len(groups))
            break
        # **조각 전체가 비면 막힌 것으로 본다** (25.601, 감사). 종목별 예외를 삼킨 빈 표는 성공처럼 와서, 차단당한
        # 뒤에도 남은 조각
        # 수천 요청을 계속 보내 차단을 늘렸다(infra 16.2 기록: 조각 6 에서 막힌 뒤 조각 11 까지 불렀다). 두 조각 잇달아
        # 비면 멈춘다
        if group and not got:
            failed_chunks += 1
            빈조각 += 1
            last_error = last_error or f"조각 {index + 1} 전체가 비었습니다(호출 제한 의심)"
            if 빈조각 >= 2:
                blocked = True
                log.warning("야후 조각이 잇달아 비어 남은 조각을 건너뜁니다 (%d/%d)", index + 1, len(groups))
                break
        else:
            빈조각 = 0

    notes: list[str] = []
    if failed_chunks:
        notes.append(f"{len(groups)}조각 중 {failed_chunks}조각 실패: {last_error}")
    if blocked:
        notes.append("호출 제한으로 중간에 멈췄습니다")
    if missing:
        sample = ", ".join(missing[:5])
        notes.append(f"시세를 받지 못한 종목 {len(missing)}개 (예: {sample})")

    return FetchResult(
        ok=bool(bars),
        data=bars,
        limit_state="blocked" if blocked else ("warn" if failed_chunks else "ok"),
        error="; ".join(notes),
    )


def _to_bars(frame: Any, tickers: list[str]) -> tuple[list[DailyBar], list[str]]:
    """다운로드 결과를 DailyBar 목록과 '못 받은 심볼' 목록으로 나눈다."""
    bars: list[DailyBar] = []
    missing: list[str] = []

    if frame is None or len(frame) == 0:
        return bars, list(tickers)

    single = len(tickers) == 1

    for ticker in tickers:
        try:
            sub = frame if single and "Close" in frame.columns else frame[ticker]
            closes = sub["Close"]
        except (KeyError, IndexError, TypeError):
            missing.append(ticker)
            continue

        rows = 0
        for stamp, close in closes.items():
            if close is None or close != close:  # NaN 은 자기 자신과 다르다
                continue
            bars.append(
                DailyBar(
                    ticker=ticker,
                    date=stamp.strftime("%Y-%m-%d"),
                    close=float(close),
                    open=_cell(sub, "Open", stamp),
                    high=_cell(sub, "High", stamp),
                    low=_cell(sub, "Low", stamp),
                    adj_close=_cell(sub, "Adj Close", stamp),
                    volume=_int_cell(sub, "Volume", stamp),
                    currency=_guess_currency(ticker),
                )
            )
            rows += 1

        if rows == 0:
            missing.append(ticker)

    return bars, missing


def _cell(sub: Any, column: str, stamp: Any) -> float | None:
    try:
        value = sub[column].get(stamp)
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    if value is None or value != value:
        return None
    return float(value)


def _int_cell(sub: Any, column: str, stamp: Any) -> int | None:
    value = _cell(sub, column, stamp)
    return None if value is None else int(value)


def _offline_bars(symbols: list[str]) -> FetchResult:
    """오프라인 모드. 네트워크를 타지 않고 고정 데이터를 돌려준다."""
    import json
    from pathlib import Path

    fixture = Path(config.ROOT) / "tests" / "fixtures" / "us_daily_bars.json"
    if not fixture.exists():
        return FetchResult(ok=False, error=f"픽스처가 없습니다: {fixture}")

    raw = json.loads(fixture.read_text(encoding="utf-8"))
    wanted = set(symbols)
    bars = [DailyBar(**row) for row in raw.get("bars", []) if row["ticker"] in wanted]
    return FetchResult(ok=bool(bars), data=bars, limit_state="ok", from_cache=True)


# ----------------------------------------------------------------------
# 실적 발표 예정일 (docs/data-sources.md 4.1, 2026-09-18 실측)
# ----------------------------------------------------------------------

# 예정일 조회는 종목당 한 번이라 일봉(조각 요청)보다 훨씬 가볍다. 그래도 2,700종목을
# 1.5초 간격으로 부르면 한 시간이 넘어 따로 둔다. 429 가 나면 호출한 쪽이 물러선다.
EARNINGS_INTERVAL = 0.3  # 추정 [확인필요: 첫 실행에서 429 가 나면 늘린다]


def parse_earnings_dates(calendar: Any) -> list[str]:
    """yfinance Ticker.calendar → 날짜 문자열 목록 (오름차순, YYYY-MM-DD).

    2026-09-18 실측: dict 이고 'Earnings Date' 가 date 목록이다. 값이 하나면 확정에 가깝고,
    둘이면 야후가 아직 모르는 **구간**(예: 10/27~10/31)이다. 판정은 호출한 쪽이 한다.
    """
    if not isinstance(calendar, dict):
        return []
    raw = calendar.get("Earnings Date")
    if raw is None:
        return []
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    out: list[str] = []
    for value in values:
        text = value.isoformat() if hasattr(value, "isoformat") else str(value)
        text = text[:10]
        if len(text) == 10 and text[4] == "-" and text[7] == "-" and text not in out:
            out.append(text)
    return sorted(out)


def fetch_earnings_dates(symbol: str) -> FetchResult:
    """한 종목의 실적 발표 예정일. data 는 날짜 문자열 목록(비어 있을 수 있다)."""
    if config.SETTINGS.offline_mode:
        return FetchResult(ok=False, error="오프라인 모드")
    try:
        import yfinance as yf

        calendar = yf.Ticker(symbol).calendar
    except Exception as exc:  # noqa: BLE001 — 야후는 종목마다 다른 예외를 던진다
        blocked = _is_rate_limit(exc)
        return FetchResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            limit_state="blocked" if blocked else "unknown",
        )
    # **빈 응답은 "날짜 없음" 이 아니다** (docs/infra.md 25.601, 감사). yfinance 는 5xx·401(crumb) 을 삼키고 `{}` 를
    # 돌려줘, 여기서
    # `ok=True, data=[]` 가 되면 호출한 쪽(`earnings_calendar`)이 "답했다" 로 보고 그 종목의 앞으로 야후 일정을 지웠다 —
    # 야후 장애 주에는
    # 전 종목의 확정 예정일이 사라졌다. 빈 표는 모르는 것으로 둔다(지우지 않는다) [확인필요: 일정이 정말 없는 종목도
    # `{}` 인지]
    if not calendar:
        return FetchResult(
            ok=False, error="예정일 응답이 비었습니다(야후 오류일 수 있어 지우지 않습니다)", limit_state="unknown"
        )
    return FetchResult(ok=True, data=parse_earnings_dates(calendar), limit_state="ok")
