"""Step 0 기반 검증.

설계를 무너뜨릴 수 있는 전제를 코드를 쌓기 전에 확인한다.
여기서 막히면 구조를 다시 짠다.

확인 항목
  1. 야후 파이낸스가 이 환경의 IP 에서 동작하는가        <- 핵심
  2. 국내 종목 장중 현재가를 받을 수 있는가, 얼마나 늦는가
  3. 텔레그램 발송이 되는가
  4. 거래소 캘린더가 동작하는가
  5. Turso 에 연결되는가
  6. 한국거래소 Open API 가 호출되는가

실행
  python -m batch.jobs.step0_check
  python -m batch.jobs.step0_check --no-telegram   (발송 없이 확인만)
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from batch import config

log = logging.getLogger("step0")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = False

    @property
    def mark(self) -> str:
        if self.ok:
            return "통과"
        return "실패(치명)" if self.blocking else "실패"


def _current_env() -> str:
    """실행 환경을 알아본다. 로컬과 Actions 결과를 비교하기 위해서다."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return f"GitHub Actions ({os.environ.get('RUNNER_OS', '?')})"
    return f"로컬 ({platform.system()})"


# ----------------------------------------------------------------------
# 1. 야후 파이낸스 일별 시세
# ----------------------------------------------------------------------
def check_yfinance_daily() -> Check:
    from batch.sources import yfinance_src

    tickers = ["005930.KS", "AAPL"]
    started = time.monotonic()
    result = yfinance_src.fetch_daily_quotes(tickers)
    elapsed = time.monotonic() - started

    if not result.ok:
        detail = f"{result.error} (limit_state={result.limit_state}, {elapsed:.1f}초)"
        return Check("야후 일별 시세", False, detail, blocking=True)

    lines = []
    for quote in result.data:
        change = quote.change_pct
        change_text = f"{change:+.2f}%" if change is not None else "전일 없음"
        lines.append(
            f"{quote.ticker} {quote.trade_date} 종가 {quote.close:,.2f} "
            f"{quote.currency} ({change_text})"
        )

    got = {q.ticker for q in result.data}
    missing = set(tickers) - got
    if missing:
        return Check(
            "야후 일별 시세",
            False,
            f"받지 못한 종목: {', '.join(sorted(missing))} | " + " / ".join(lines),
            blocking=True,
        )

    return Check("야후 일별 시세", True, f"{elapsed:.1f}초 | " + " / ".join(lines))


# ----------------------------------------------------------------------
# 2. 국내 장중 현재가와 지연 폭
# ----------------------------------------------------------------------
def check_kr_intraday() -> Check:
    """국내 장중 현재가를 받아 데이터 시각이 얼마나 뒤처지는지 잰다.

    한국투자증권을 쓰지 않기로 해서 이것이 국내 장중의 유일한 경로다.
    장이 닫혀 있으면 지연을 잴 수 없으므로 실패로 보지 않는다.
    """
    if config.SETTINGS.offline_mode:
        return Check("국내 장중 현재가", True, "오프라인 모드, 건너뜀")

    try:
        import yfinance as yf
    except ImportError as exc:
        return Check("국내 장중 현재가", False, f"yfinance 없음: {exc}")

    try:
        ticker = yf.Ticker("005930.KS")
        frame = ticker.history(period="1d", interval="1m", auto_adjust=False)
    except Exception as exc:  # noqa: BLE001
        return Check("국내 장중 현재가", False, f"분봉 조회 실패: {exc}")

    if frame is None or len(frame) == 0:
        return Check(
            "국내 장중 현재가",
            True,
            "분봉이 비어 있습니다. 장이 닫혀 있으면 정상입니다. "
            "정규장 중에 다시 실행해 지연 폭을 재세요",
        )

    last_ts = frame.index[-1]
    last_price = float(frame["Close"].iloc[-1])
    try:
        lag_sec = (
            datetime.now(UTC) - last_ts.tz_convert("UTC").to_pydatetime()
        ).total_seconds()
        lag_text = f"약 {lag_sec / 60:.0f}분 뒤처짐"
    except Exception:  # noqa: BLE001 - 시간대 정보가 없을 수 있다
        lag_text = "지연 계산 불가"

    return Check(
        "국내 장중 현재가",
        True,
        f"마지막 분봉 {last_ts} 가격 {last_price:,.0f} | {lag_text} "
        f"| 봉 개수 {len(frame)}",
    )


# ----------------------------------------------------------------------
# 3. 거래소 캘린더
# ----------------------------------------------------------------------
def check_calendar() -> Check:
    try:
        import exchange_calendars as xcals
    except ImportError as exc:
        return Check("거래소 캘린더", False, f"불러오기 실패: {exc}")

    try:
        parts = []
        for code in ("XKRX", "XNYS"):
            cal = xcals.get_calendar(code)
            today = datetime.now(UTC).date()
            is_open = cal.is_session(today.isoformat())
            parts.append(f"{code} 오늘 {'개장' if is_open else '휴장'}")
        version = getattr(xcals, "__version__", "버전 미상")
        return Check("거래소 캘린더", True, f"v{version} | " + ", ".join(parts))
    except Exception as exc:  # noqa: BLE001
        return Check("거래소 캘린더", False, str(exc))


# ----------------------------------------------------------------------
# 4. 한국거래소 Open API
# ----------------------------------------------------------------------
def check_krx() -> Check:
    """직전 거래일의 KOSPI 전종목 일별매매정보를 실제로 받아 본다.

    국내 일별 시세의 1순위 경로다. 여기가 막히면 야후로 폴백해야 한다.
    """
    key = config.get("KRX_API_KEY")
    if not key:
        return Check("한국거래소 API", False, "KRX_API_KEY 가 비어 있습니다")

    if config.SETTINGS.offline_mode:
        return Check("한국거래소 API", True, "오프라인 모드, 건너뜀")

    from batch.sources import krx

    try:
        bas_dd = krx.previous_session_yyyymmdd()
    except Exception as exc:  # noqa: BLE001
        return Check("한국거래소 API", False, f"직전 거래일 계산 실패: {exc}")

    started = time.monotonic()
    result = krx.fetch_daily("KOSPI", bas_dd)
    elapsed = time.monotonic() - started

    if not result.ok:
        return Check("한국거래소 API", False, f"{bas_dd} 조회 실패: {result.error}")

    rows = result.data
    if not rows:
        return Check(
            "한국거래소 API",
            False,
            f"{bas_dd} 응답이 빈 배열입니다. 휴장일이거나 아직 집계 전일 수 있습니다",
        )

    samsung = next((r for r in rows if r.isu_cd == "005930"), None)
    sample = (
        f"삼성전자 종가 {samsung.close:,.0f} 거래대금 {samsung.value:,}"
        if samsung and samsung.close and samsung.value
        else f"첫 행 {rows[0].isu_nm}"
    )
    return Check(
        "한국거래소 API",
        True,
        f"{bas_dd} KOSPI {len(rows)}종목 {elapsed:.1f}초 | {sample}",
    )


# ----------------------------------------------------------------------
# 5. Turso 연결
# ----------------------------------------------------------------------
def check_turso() -> Check:
    url = config.get("TURSO_DATABASE_URL")
    token = config.get("TURSO_AUTH_TOKEN")
    if not url or not token:
        return Check(
            "Turso 연결",
            False,
            "아직 계정을 만들지 않았습니다. TURSO_DATABASE_URL 과 "
            "TURSO_AUTH_TOKEN 이 비어 있습니다",
        )

    from batch.core.turso import TursoClient, TursoError

    now = datetime.now(UTC).isoformat()
    try:
        with TursoClient() as client:
            client.batch(
                [
                    (
                        "CREATE TABLE IF NOT EXISTS step0_check "
                        "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        " checked_at TEXT NOT NULL, env TEXT NOT NULL)",
                        [],
                    ),
                    (
                        "INSERT INTO step0_check (checked_at, env) VALUES (?, ?)",
                        [now, _current_env()],
                    ),
                ]
            )
            rs = client.execute(
                "SELECT COUNT(*), MAX(checked_at) FROM step0_check"
            )
            count, latest = rs.rows[0]
            version = client.execute("SELECT sqlite_version()").scalar()
    except TursoError as exc:
        return Check("Turso 연결", False, str(exc))
    except Exception as exc:  # noqa: BLE001
        return Check("Turso 연결", False, f"{type(exc).__name__}: {exc}")

    return Check(
        "Turso 연결",
        True,
        f"쓰기·읽기 성공. SQLite {version} | 누적 {count}행, 최근 {latest}",
    )


# ----------------------------------------------------------------------
# 6. 텔레그램
# ----------------------------------------------------------------------
def send_report(checks: list[Check]) -> Check:
    """검증 결과를 텔레그램으로 보낸다.

    대화방 번호를 아직 모르는 것은 설계 문제가 아니라 준비 단계다.
    봇에게 먼저 말을 걸어야 알 수 있기 때문이다. 이 경우는 치명으로 보지 않는다.
    발송 자체가 막히는 것만 치명으로 본다.
    """
    from batch.notify import telegram

    passed = sum(1 for c in checks if c.ok)
    header = (
        f"Step 0 기반 검증\n"
        f"환경: {_current_env()}\n"
        f"시각: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC\n"
        f"결과: {passed}/{len(checks)} 통과\n"
    )
    body = "\n".join(f"[{c.mark}] {c.name}\n  {c.detail}" for c in checks)

    try:
        chat_id = telegram.resolve_chat_id()
    except telegram.TelegramError as exc:
        return Check(
            "텔레그램 발송",
            False,
            f"{exc} 봇 이름은 @churoong_stock_bot 입니다",
            blocking=False,
        )

    try:
        ids = telegram.send(f"{header}\n{body}", chat_id=chat_id)
        return Check("텔레그램 발송", True, f"chat_id 확인, message_id {ids}")
    except Exception as exc:  # noqa: BLE001
        return Check("텔레그램 발송", False, str(exc), blocking=True)


# ----------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Step 0 기반 검증")
    parser.add_argument(
        "--no-telegram", action="store_true", help="텔레그램으로 보내지 않는다"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=config.SETTINGS.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    print(f"실행 환경: {_current_env()}")
    print(f"오프라인 모드: {config.SETTINGS.offline_mode}")
    print("-" * 60)

    checks = [
        check_yfinance_daily(),
        check_kr_intraday(),
        check_calendar(),
        check_krx(),
        check_turso(),
    ]

    if not args.no_telegram:
        checks.append(send_report(checks))

    print("-" * 60)
    for check in checks:
        print(f"[{check.mark}] {check.name}")
        print(f"        {check.detail}")

    blocking_failed = [c for c in checks if not c.ok and c.blocking]
    optional_failed = [c for c in checks if not c.ok and not c.blocking]

    print("-" * 60)
    print(f"통과 {sum(1 for c in checks if c.ok)}/{len(checks)}")
    if optional_failed:
        print("미완료(치명 아님): " + ", ".join(c.name for c in optional_failed))
    if blocking_failed:
        print("치명적 실패: " + ", ".join(c.name for c in blocking_failed))
        print("이 항목이 막히면 설계를 다시 검토해야 합니다.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
