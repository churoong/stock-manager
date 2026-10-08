"""일일 배치.

흐름 (Step 9, 2026-09-17)
  1. 전일 시세 수집
  2. 점수(scores) → 신호(signals) 다시 계산. 기준일은 그 거래일이다
  3. 리포트: 대표 종목 종가 + 추천 1부 개별 종목 · 2부 포트폴리오 (design.md 3.9)
  4. 텔레그램 발송

점수·신호는 원래 주 1회 따로 돌았다. 그러면 아침 리포트의 추천이 최대 일주일
묵는다. 둘 다 1분 안쪽이라 일일 배치 안으로 옮겼다. 주간 워크플로는 수동 재계산용으로 남긴다.

실행
  python -m batch.jobs.daily --market KR
  python -m batch.jobs.daily --market US --force     예약 시각과 무관하게 실행
  python -m batch.jobs.daily --market KR --dry-run   발송하지 않고 출력만

설계상 중요한 점
  - 예약 실행은 지연되거나 건너뛰므로, 돌아야 할 때인지 스스로 판단한다
  - 같은 거래일에 이미 성공했으면 다시 돌지 않는다. 중복 발송을 막는다
  - 실패하면 텔레그램으로 알리고, 마지막 성공 데이터는 그대로 둔다
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import replace as dc_replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import client as backend
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import NOTHING_DONE, guard
from batch.notify import formatter, report_sections, telegram
from batch.services import divergence, holding_scores, pick_history, report_picks, reports, self_grade, trend

log = logging.getLogger("daily")

JOB_PREFIX = "daily"

# D1 임시 운영 중 미국 일일 배치가 남기는 건너뜀 사유 (docs/infra.md 25.14)
US_PAUSED_ON_D1 = "D1 임시 운영 중에는 미국을 돌리지 않습니다 (docs/infra.md 25.11). Turso 로 돌아가면 다시 돈다"

# 미국 일봉을 몇 거래일치 받을지.
#
# 하루치만 받으면 실패한 날이 영영 빈다. 20일 평균 거래대금은 하루만 비어도
# 그 종목을 판정에서 통째로 뺀다. 창을 넉넉히 두면 다음 실행이 알아서 메우고,
# 같은 응답에 실려 오므로 호출 수는 늘지 않는다.
#
# 환경변수로 바꿀 수 있게 한 이유 (2026-09-16): 수정주가(adj_close) 때문이다.
# 야후의 수정주가는 배당·분할이 생길 때마다 **과거 값이 전부 다시 조정된다.**
# 매일 10일치만 덮어쓰면 최근 10일과 그 이전 행의 조정 기준이 어긋난다.
# 일관된 계열을 만들려면 전 구간을 한 번에 받아야 한다.
#
#   US_LOOKBACK_DAYS=1300 python -m batch.jobs.daily --market US --force --dry-run
#
# 이걸 정기적으로(예: 주 1회) 돌리는 장치는 아직 없다 [확인필요: 주기와 비용].
# 그때까지는 배당·분할 직후에 손으로 한 번 돌린다.
US_LOOKBACK_DAYS = int(config.get("US_LOOKBACK_DAYS", "") or 10)

# 리포트에 싣는 대표 종목.
#
# 수집 대상이 아니다. 수집은 두 시장 모두 마스터 전종목으로 돈다.
# 이 목록은 "매일 아침 메시지에 숫자로 뜨는 종목"일 뿐이고,
# 추천 종목 선정이 붙는 Step 9 에서 대체된다.
WATCHED = {
    "KR": [
        {
            "ticker": "005930",
            "market": "KOSPI",
            "country": "KR",
            "currency": "KRW",
            "yahoo_symbol": "005930.KS",
            "name_ko": "삼성전자",
            "name_en": "Samsung Electronics",
        }
    ],
    "US": [
        {
            "ticker": "AAPL",
            "market": "NASDAQ",
            "country": "US",
            "currency": "USD",
            "yahoo_symbol": "AAPL",
            "name_ko": "애플",
            "name_en": "Apple Inc.",
        }
    ],
}


def job_name(market: str) -> str:
    return f"{JOB_PREFIX}_{market.lower()}"


# `_trigger()` 는 여기 있었다. **한 곳에만 있어서 나머지 스물몇 작업이 "manual" 을 박았다** —
# `batch/core/db.trigger_source()` 로 옮겼다 (2026-09-21, docs/infra.md 25.95).


def _kis_calendar_check(client: TursoClient, today: date) -> list[str]:
    """KIS 휴장일 조회와 우리 달력이 다른 날을 경고로 (25.985). 키가 없으면 조용히 건너뛴다. **곁다리다** — 실패해도
    시세·리포트는 그대로 가고, 실패 사실만 한 줄 남긴다(값·토큰은 남기지 않는다)."""
    from batch.sources import kis

    if not kis.configured():
        return []
    try:
        statuses, calls = kis.holidays(client, today)
        db.record_api_call(client, "kis_openapi", count=calls)  # = kis.SOURCE (이름은 글자로, 25.983)
    except (kis.KisFailed, OSError, KeyError) as exc:  # requests 의 접속 오류도 OSError 계열이다
        return [f"KIS 휴장일 대조를 못 했습니다 ({exc}) — 달력은 exchange_calendars 그대로 씁니다"]
    다름 = cal.compare_with_kis("KR", statuses, today)
    if not 다름:
        return []
    return [
        "⚠️ 휴장일 판정이 KIS 공식 조회와 다릅니다: " + " / ".join(다름)
        + " — 임시공휴일이면 batch/core/calendar.EXTRA_HOLIDAYS 에 넣어야 합니다"
    ]


def _까닭(warnings: list[str], limit: int = 5) -> str:
    """실패 예외에 붙일 경고 요약 — 앞 몇 줄만 (25.615). 두 시장 × (한도 + 폴백 실패) 가 넷이라 다섯 줄 (25.619)."""
    return (" — " + " / ".join(warnings[:limit])) if warnings else ""


class KrxNotYet(RuntimeError):
    """거래소 전일 시세가 아직 공개 전이다 — 실패가 아니라 이른 실행이다 (25.604)."""


#: 이 시각(KST) 전의 빈 응답은 "아직 공개 전" 으로 본다. 16.1 실측: 07:56 빈 응답, 08:00 전후 공개. 정규 예약 08:27 은
#: 이 뒤다
KRX_DAILY_READY_KST = (8, 15)


def _krx_아직_이른가(now: datetime | None = None) -> bool:
    지금 = (now or datetime.now(UTC)).astimezone(cal.market_tz("KR"))
    return (지금.hour, 지금.minute) < KRX_DAILY_READY_KST


def collect_kr_prices(
    client: TursoClient, trade_date: str
) -> tuple[list[dict[str, Any]], list[str], str]:
    """국내 전종목 시세를 한국거래소에서 받아 저장한다.

    돌려주는 것은 (리포트 행, 경고, 요약) 이다. **요약은 경고가 아니다.**
    예전에는 "국내 N종목 저장" 을 경고 목록 맨 앞에 넣어, 아무 문제가 없어도
    실행 상태가 항상 partial 이 되고 텔레그램에 "경고" 로 찍혔다(2026-09-17 발견).

    호출 한 번에 시장별 전종목이 온다. 야후처럼 종목 수에 비례해 호출이
    늘지 않는다. 거래대금과 시가총액도 함께 주므로 유니버스 판정에도 쓴다.

    야후는 폴백으로만 둔다. 국내 데이터는 거래소가 공식 원천이다.
    """
    from batch.sources import krx

    bas_dd = trade_date.replace("-", "")
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    stored_total = 0
    받은행: list[Any] = []

    ids = _kr_stock_ids(client)
    if not ids:
        raise RuntimeError("국내 종목 마스터가 비어 있습니다. 유니버스를 먼저 돌리세요")

    for market_name in ("KOSPI", "KOSDAQ"):
        if db.usage_blocked_today(client, "krx_openapi"):
            # 한도가 찬 날은 부르지 않고 바로 야후로 대신한다 (25.606 — 25.387 과 같은 모양)
            warnings.append(f"{market_name} 거래소 일일 호출 한도에 도달해 부르지 않았습니다")
            대신, 경고 = _kr_yahoo_fallback(client, market_name, trade_date)
            stored_total += 대신
            warnings.extend(경고)
            continue
        result = krx.fetch_daily(market_name, bas_dd)
        # 재시도까지 센다 (docs/infra.md 25.390)
        db.record_api_call(
            client, "krx_openapi", count=result.attempts, limit_value=krx.DAILY_LIMIT, warn_at_pct=80
        )

        # **거래소가 아직 공개하지 않은 시각이면 야후로 대신하지 않는다** (docs/infra.md 25.604, 감사).
        # 전일 시세는 08:00 전후에 나온다(16.1). 그 전(수동 실행·Actions 예비 cron)에 빈 응답을 받으면 25.505 폴백이
        # 야후로 그날을 닫아(거래대금·등락률 없음 — 분할 감지도 못 돈다) 08:27 정규 실행이 "이미 성공" 으로
        # 건너뛰었다. 이르면 건너뛰기로 닫고 다음 실행에 맡긴다
        if result.ok and not result.data and _krx_아직_이른가():
            raise KrxNotYet(
                f"{market_name} {trade_date} 시세가 아직 공개되지 않았습니다(08:15 KST 전) — 다음 실행이 받습니다"
            )
        if not result.ok or not result.data:
            warnings.append(
                f"{market_name} 수집 실패: {result.error}" if not result.ok
                else f"{market_name} {trade_date} 응답이 비었습니다"
            )
            # **야후로 대신 받는다** (docs/data-sources.md 2절, docs/infra.md 25.505). 예전에는 경고만 쌓고, 두 시장이
            # 다 실패하면 리포트가 통째로 안 나갔다 — 설계서가 약속한 "한국거래소 → 야후" 폴백이 없었다
            대신, 경고 = _kr_yahoo_fallback(client, market_name, trade_date)
            stored_total += 대신
            warnings.extend(경고)
            continue

        stored = _store_krx_day(client, result.data, trade_date, ids, result.source)
        stored_total += stored
        받은행.extend(result.data)
        # 앞서 야후로 대신 받은 날을 거래소 값으로 바꾼다 (docs/infra.md 25.513, 교차검증)
        warnings.extend(_heal_yahoo_days(client, market_name, trade_date, ids))

    # **추천·보유 ETF 의 종가** (docs/infra.md 25.896). 이은 ETF 가 있을 때만 한 번 부른다(하루 전 ETF 가 한 호출에
    # 온다)
    warnings.extend(_store_kr_etf_day(client, bas_dd, trade_date))

    # 캘린더 판정을 남기고, 판정과 실제 데이터가 어긋나는지 본다. 임시공휴일 반영이 늦으면 여기서 드러난다.
    # 앞날은 KIS 공식 휴장일 조회와 견준다(아래, 25.985).
    from datetime import date as _date

    trade_day = _date.fromisoformat(trade_date)
    cal.record_decision(client, "KR", trade_day, is_open=True)
    suspicion = cal.suspect_calendar(is_open=True, rows_collected=stored_total)
    if suspicion:
        warnings.append(suspicion)
    # 앞으로 2주 휴장일을 KIS 공식 조회와 견준다 (docs/infra.md 25.985) — 임시공휴일을 그날이 오기 전에 안다
    warnings.extend(_kis_calendar_check(client, cal.user_today()))

    if stored_total == 0:
        # **까닭을 함께 올린다** (docs/infra.md 25.615). 거래소 한도·야후 폴백 실패 같은 원인은 경고에만 있어,
        # 실패 알림에는 "한 건도 저장하지 못했습니다" 만 갔다
        raise RuntimeError(f"{trade_date} 국내 시세를 한 건도 저장하지 못했습니다" + _까닭(warnings))

    # **기업행위가 있었으면 말한다** (docs/infra.md 25.138). 수정주가는 예약이 없어
    # 사람이 돌려야 하는데, 돌려야 한다는 사실을 아무도 알려 주지 않고 있었다
    # **알려 주는 일이 배치를 죽이지 않는다** (docs/infra.md 25.246). 시세는 이미 저장됐다 — 여기서 읽기가 한 번
    # 실패했다고 리포트 전체가 나가지 않으면 안 된다. 미국 쪽(`find_drift`)은 처음부터 감싸 두었다
    try:
        warnings.extend(_detect_kr_actions(client, trade_date, 받은행, ids))
    except Exception as exc:  # noqa: BLE001 — 경고를 내는 길이다. 실패는 경고로 남긴다
        log.warning("기업행위 확인 실패: %s", exc)
        warnings.append(f"기업행위(분할 등) 확인을 하지 못했습니다: {exc}")
    warnings.extend(_collect_index_prices(client))

    summary = f"국내 {stored_total:,}종목 저장"

    # 리포트에는 지켜보는 종목만 싣는다. 추천 선정은 Step 9 에서 붙는다.
    rows = _report_rows_from_db(client, trade_date, WATCHED["KR"])
    return rows, warnings, summary


def report_regimes(
    client: TursoClient, market: str, signals_as_of: str | None, trade_date: str
) -> tuple[dict[str, trend.Regime], str]:
    """리포트 머리의 국면 줄. **금액을 줄인 그날의 국면**으로 낸다 (docs/infra.md 25.197).

    그 줄은 "약세 지수의 신규 매수 비중 ×0.50" 이라고 **아래 금액을 설명한다.** 금액은
    신호 작업이 `signals_as_of` 의 국면으로 줄인 것이다. 신호가 오늘 계산되지 않은 날
    ("추천은 … 기준입니다 (오늘 계산분 없음)") 오늘 거래일로 국면을 다시 내면, 줄은 "약세 ×0.50" 인데
    금액은 안 줄었거나 그 반대가 된다. 신호가 없으면 거래일로 낸다 — 설명할 금액이 없다.
    """
    regimes = trend.regimes_for_country(client, "KR" if market == "KR" else "US", signals_as_of or trade_date)
    return regimes, trend.report_line(regimes, trend.load_settings(client))


def _collect_index_prices(client: TursoClient) -> list[str]:
    """지수 일봉(추세 필터용, docs/signals.md 3.5). 실패해도 시세는 살린다 — 국면이 '미판정' 으로 남을 뿐."""
    from batch.jobs import index_prices as index_job

    try:
        _, warnings = index_job.collect(client)
    except Exception as exc:  # noqa: BLE001
        return [_실패문("지수 수집", exc)]
    return warnings


#: 일일 배치가 기업행위로 보인 종목의 수정주가를 바로 다시 내는 상한 (25.1009) — 종목당 약 1,250행
#: (`adjust_kr.ROWS_PER_STOCK`)이라 20종목이면 2.5만 행이다. 이보다 많으면 수집 이상일 수 있어 사람에게 넘긴다
AUTO_ADJUST_MAX_STOCKS = 20


def _detect_kr_actions(
    client: TursoClient, trade_date: str, krx_rows: list, ids: dict[str, int]
) -> list[str]:
    """그날 **기업행위로 보이는 종목**을 찾아 말한다 (docs/infra.md 25.138).

    국내 수정주가(`jobs/adjust_kr`)는 **예약이 없다.** `adjust-kr.yml` 은
    `workflow_dispatch` 뿐이고 일일 배치도 부르지 않는다 — 사람이 손으로 돌릴 때만 돈다.
    그 사이에 액면분할이 나면 `COALESCE(adj_close, close)` 계열이 **그날 -90% 로 꺾이고**,
    모멘텀·변동성·MDD·베타·백테스트가 전부 그 값을 본다.

    **매일 다시 내지는 못한다.** 전 종목 수정주가는 종목당 1,250행을 읽어
    (`adjust_kr.ROWS_PER_STOCK`) 2,600종목이면 **하루 읽기 예산의 3분의 2**다.
    그래서 **싼 쪽**을 매일 한다 — 그날치 계수만 보고, 걸리면 사람에게 말한다.
    그날 두 행(어제 종가·오늘 종가+등락률)이면 되고 `idx_prices_date` 를 탄다.

    계수 식은 "앞 행 = 바로 전 거래일" 을 전제하므로(25.132) 사이가 벌어져 있으면
    **기업행위라고 단정하지 않고 그 사실을 함께 적는다.**
    """
    from batch.services import adjust as adj
    from batch.services.metrics import MAX_SESSION_GAP_DAYS

    직전 = client.execute(
        "SELECT MAX(p.date) FROM prices p JOIN stocks s ON s.id = p.stock_id"
        " WHERE s.country = 'KR' AND p.date < ?",
        [trade_date],
    ).scalar()
    if not 직전:
        return []
    prev = {
        int(r[0]): float(r[1])
        for r in client.execute(
            "SELECT p.stock_id, p.close FROM prices p JOIN stocks s ON s.id = p.stock_id"
            " WHERE s.country = 'KR' AND p.date = ? AND p.close IS NOT NULL",
            [str(직전)],
        ).rows
    }
    # **전날 행이 없는 종목은 제 마지막 종가로 본다** (docs/infra.md 25.246). 국내 분할은 대개 **거래정지**를 끼고
    # (25.132), 정지한 날은 행이 없거나 종가 0 이라 버려진다(25.203). 그래서 재개한 날 그 종목은 시장 전체의 직전
    # 거래일에 행이 없어 **건너뛰어졌다** — 분할을 가장 알아채야 하는 종목이 빠졌다. 빠진 종목(대개 몇 개)만 따로 읽는다
    빠진 = sorted(
        {sid for row in krx_rows if (sid := ids.get(row.isu_cd)) is not None and sid not in prev and row.close}
    )
    제_직전: dict[int, str] = {}
    묶음 = db.in_chunk(reserve=1)
    for i in range(0, len(빠진), 묶음):
        조각 = 빠진[i : i + 묶음]
        자리 = ", ".join("?" * len(조각))
        for sid, 날, 종가 in client.execute(
            "SELECT q.stock_id, q.date, q.close FROM prices q"
            f" WHERE q.stock_id IN ({자리}) AND q.close > 0 AND q.date = ("
            "   SELECT MAX(r.date) FROM prices r WHERE r.stock_id = q.stock_id AND r.date < ? AND r.close > 0)",
            [*조각, trade_date],
        ).rows:
            prev[int(sid)] = float(종가)
            제_직전[int(sid)] = str(날)

    from datetime import date as _date

    간격 = (_date.fromisoformat(trade_date) - _date.fromisoformat(str(직전))).days

    걸린것: list[str] = []
    걸린번호: list[int] = []
    for row in krx_rows:
        stock_id = ids.get(row.isu_cd)
        before = prev.get(stock_id) if stock_id is not None else None
        if before is None or row.close is None:
            continue
        if adj.is_action(adj.factor_of(before, row.close, row.change_pct)):
            # 제 마지막 종가로 봤으면 그 날짜를 붙인다 — 정지 뒤 재개라는 뜻이다
            걸린것.append(
                f"{row.isu_cd}(정지 뒤 재개, 앞 종가 {제_직전[stock_id]})" if stock_id in 제_직전 else str(row.isu_cd)
            )
            if stock_id is not None:
                걸린번호.append(stock_id)
    if not 걸린것:
        return []

    보기 = ", ".join(sorted(걸린것)[:5]) + ("…" if len(걸린것) > 5 else "")
    # **빠진 거래일로 본다** (docs/infra.md 25.569, 감사 재현). 11일 문턱만 보면 하루 수집 구멍(간격 2~4일)은
    # 단서 없이 "기업행위 수백 개" 가 됐다 — 보유 종목이면 멀쩡한 매매 기록을 고치라는 안내까지 붙었다
    try:
        진짜_직전 = cal.previous_session("KR", _date.fromisoformat(trade_date)).isoformat()
    except Exception:  # noqa: BLE001 — 달력을 못 쓰면 날짜 간격만 본다
        진짜_직전 = str(직전)
    꼬리 = (
        f" 다만 직전 거래일({진짜_직전})의 시세가 없고 {직전} 종가와 견줬습니다"
        " — 수집 구멍이면 기업행위가 아닐 수 있습니다."
        " 그날 시세를 먼저 되받으세요(Actions → \"국내 백필\")."
        if str(직전) < 진짜_직전
        else f" 다만 직전 거래일({직전})과 {간격}일 벌어져 있어 기업행위가 아닐 수 있습니다."
        if 간격 > MAX_SESSION_GAP_DAYS
        else ""
    )
    if str(직전) < 진짜_직전:
        # **수집 구멍 뒤에는 수정주가·수량 안내를 붙이지 않는다** (docs/infra.md 25.572, 교차검증).
        # 25.569 는 꼬리 문구만 바꿔, 여전히 "수정주가를 다시 내야 합니다" 와 보유 종목의 "매매 기록 수량을
        # 확인하세요"(25.146)가 붙었다 — 멀쩡한 매매 기록을 고치게 만드는 말이다. 먼저 그날 시세를 되받게 한다
        # 보유 종목 안내는 **단서를 달아 남긴다** (25.574, 교차검증). 빼 버리면 두 시장이 다 빈 날 다음의 진짜 분할은
        # 다시 알아챌 길이 없어(되받기 대상도 아니다) 25.146 이 막으려던 거짓 손절 알림으로 이어졌다
        단서 = "(직전 거래일 시세가 없어 분할인지 확실하지 않습니다)"
        보유말 = [f"{단서} {m}" for m in _보유_수량_확인(client, 걸린번호)]
        return [f"직전 거래일 시세가 빠진 채 크게 움직인 종목 {len(걸린것)}개: {보기}.{꼬리}", *보유말]
    # **걸린 종목만 바로 다시 낸다** (25.1009). 전 종목은 하루 읽기 예산의 3분의 2 라 손으로 돌리게 했지만,
    # 걸린 몇 종목은 종목당 약 1,250행이라 싸다. 예전에는 사람이 Actions 를 돌릴 때까지 그 종목 수정종가 계열이
    # 그날 -50%·-90% 로 꺾인 채 모멘텀·변동성·MDD·점수에 들어갔다.
    # 사이가 벌어진 날(위 꼬리)은 기업행위라 단정하지 않으므로 내지 않는다
    if not 꼬리 and 0 < len(걸린번호) <= AUTO_ADJUST_MAX_STOCKS:
        try:
            from batch.jobs import adjust_kr

            결과 = adjust_kr.adjust_stocks(client, sorted(set(걸린번호)))
            말들 = [
                f"기업행위로 보이는 종목 {len(걸린것)}개: {보기}. 그 종목만 국내 수정주가를 바로 다시 냈습니다"
                f"(바뀐 행 {결과['rows_updated']:,})"
            ]
        except Exception as exc:  # noqa: BLE001 — 못 내면 예전처럼 사람에게 말한다
            말들 = [
                f"기업행위로 보이는 종목 {len(걸린것)}개: {보기}. 수정주가를 바로 내지 못했습니다({exc})"
                " **국내 수정주가를 다시 내야 합니다** — Actions → \"국내 수정주가\""
            ]
        말들.extend(_보유_수량_확인(client, 걸린번호))
        return 말들
    말들 = [
        f"기업행위로 보이는 종목 {len(걸린것)}개: {보기}.{꼬리}"
        " **국내 수정주가를 다시 내야 합니다** — Actions → \"국내 수정주가\""
    ]
    말들.extend(_보유_수량_확인(client, 걸린번호))
    return 말들


def _보유_수량_확인(client: TursoClient, 걸린번호: list[int]) -> list[str]:
    """걸린 종목 중 **보유 중인 것**을 따로 짚는다 (docs/infra.md 25.146).

    수정주가는 *가격*을 고친다. 그런데 분할·병합은 **수량**도 바꾸는데 `trades` 는
    사용자가 적은 값이고 **시스템이 고치지 않는다**(CLAUDE.md 절대 규칙). 그래서 1:2
    분할이 나면 수량은 그대로, 종가만 반으로 떨어져 **평가액이 조용히 절반**이 된다.

    그 절반짜리 평가액이 그대로 매도 플래그에 들어간다 — `market_value / cost` 가
    -50% 가 되어 **거짓 손절 알림**이 뜬다(병합이면 거짓 목표 도달). 사용자가 가장
    믿어서는 안 될 때 가장 급해 보이는 알림이 간다.

    고치는 것은 사용자다. 우리가 할 일은 **그날 말해 주는 것**뿐이다.
    """
    if not 걸린번호:
        return []
    # **보유 목록을 통째로 읽고 파이썬에서 겹친다.** `IN (?, ?, …)` 로 종목 번호를 묶어
    # 보내면 걸린 종목이 많은 날 파라미터 한도(D1 100개)를 넘는다(25.107). 보유는
    # 개인 계좌라 수십 줄이고, 걸린 종목은 드물게 수백일 수 있다 — 작은 쪽을 읽는다
    try:
        보유 = {
            int(r[0]): str(r[1])
            for r in client.execute(
                "SELECT p.stock_id, COALESCE(s.ticker, CAST(p.stock_id AS TEXT))"
                " FROM positions p JOIN stocks s ON s.id = p.stock_id WHERE p.quantity > 0"
            ).rows
        }
    except Exception as exc:  # noqa: BLE001 — 말해 주는 일이 배치를 죽이면 안 된다
        # 표가 없으면(포트폴리오를 안 쓰는 DB) 조용하다. **그 밖의 실패는 말한다** (docs/infra.md 25.219, 25.164)
        if db.표가_없나(exc):
            return []
        return [f"기업행위 종목이 보유와 겹치는지 보지 못했습니다 — 보유를 읽지 못함: {exc}"]
    겹침 = sorted({보유[n] for n in 걸린번호 if n in 보유})
    if not 겹침:
        return []
    return [
        f"그중 **보유 중인 종목**이 있습니다: {', '.join(겹침)}."
        " 분할·병합·**무상증자**면 **매매 기록의 수량을 확인하세요** — 수량은 사용자가 적은 값이라"
        " 시스템이 고치지 않습니다. 그대로 두면 평가액·수익률이 틀어지고,"
        " 그 값으로 손절·목표 플래그가 판정됩니다. " + SPLIT_GUIDE + " " + RIGHTS_NOTE
    ]


#: 분할·병합 뒤 매매 기록을 고치는 방법 (docs/portfolio.md 7절, docs/infra.md 25.400).
#: 예전 안내는 "수량을 늘려 적는다" 뿐이었다. 체결가를 그대로 두고 수량만 늘리면 원가가 배로 늘고,
#: 늘어난 만큼을 0원 매수로 따로 넣을 수도 없다(체결가 0 은 거절). **분할 전의 매매 전부**를 같은 날짜로
#: 수량 × 비율, 체결가 ÷ 비율로 다시 넣어야 원가·실현손익이 그대로 남는다. 수수료·세금은 그대로.
SPLIT_GUIDE = (
    "고치는 법: 분할(병합) 날짜 **이전의 그 종목 매매를 모두** 지우고, 같은 날짜로"
    " 수량 × 비율, 체결가 ÷ 비율로 다시 넣으세요(1:2 분할이면 수량 ×2, 체결가 ÷2). 수수료·세금은 그대로입니다"
)


#: **유상증자 권리락은 수량을 고치지 않는다** (docs/portfolio.md 7절, docs/infra.md 25.510, 감사).
#: 거래소 등락률로 되살린 수정주가는 권리락도 조정하므로(docs/adjust.md 1장) 포트폴리오 평가 곡선은 권리락 **전**
#: 날짜를 낮춰 잡는다 — 매수일에 가짜 하락이 찍히고 권리락 날의 실제 하락은 사라진다. 최종 평가액은 맞다.
#: 계수만으로는 유상증자와 작은 무상증자를 가를 수 없어 계산은 그대로 두고 알린다
RIGHTS_NOTE = (
    "유상증자 권리락이면 수량은 그대로 두세요(청약해 받은 주식만 새 매수로 넣습니다)."
    " 이 경우 권리락 전 날짜의 평가액 곡선이 낮게 그려집니다 — 오늘 평가액은 맞습니다"
)


def _collect_fx() -> list[str]:
    """환율 한 번. **포트폴리오를 다시 계산하기 전에** 받는다 (docs/infra.md 25.137).

    **환율은 시장이 아니라 포트폴리오의 재료다.** 그런데 받는 곳이 `collect_us_prices`
    안뿐이었다 — "미국 권장 금액을 달러로 내려면 환율이 필요하다" 가 이유였다.
    그래서 **미국 배치가 쉬면(25.14) 아무도 환율을 안 받고**, 바로 뒤의 `portfolio.run`
    이 미국 보유분을 몇 주 묵은 환율로 평가한다. 25.83 이 복귀 경로에서 찾은 것과
    같은 모양이 일일 경로에 하나 더 있었던 셈이다.

    두 시장 배치가 다 불러도 같은 날짜를 덮어쓸 뿐이라 해롭지 않다 — 야후 호출 한 번,
    몇 행이다. 실패해도 경고로 돌려주고 나머지는 계속한다.
    """
    from batch.jobs import fx as fx_job

    client = None
    try:
        # **클라이언트를 여는 것까지 try 안이다.** 밖에 두면 환경변수가 비었을 때
        # 예외가 그대로 올라가 **일일 배치 전체가 죽는다** — 환율 하나 때문에
        client = TursoClient()
        db.apply_migrations(client)
        _, warnings = fx_job.collect(client)
        return warnings
    except Exception as exc:  # noqa: BLE001 — 환율 실패로 보유 평가를 멈추지 않는다
        log.exception("환율 수집 실패")
        return [_실패문("환율 수집", exc)]
    finally:
        if client is not None:
            client.close()


def _store_kr_etf_day(client: TursoClient, bas_dd: str, trade_date: str) -> list[str]:
    """`stocks` 에 이은 국내 ETF(`asset_type = 'etf'`)의 그날 시세를 한국거래소 ETF 일별에서 받아 저장한다 (25.896).

    이은 ETF 가 없으면 부르지 않는다. 실패는 경고로 남기고 배치를 멈추지 않는다 — 주식 시세는 이미 저장됐다.
    수정주가는 비운다(분할·병합이 드물고, 있어도 `adjust_kr` 이 등락률로 되살린다).
    """
    from batch.sources import krx

    rs = client.execute(
        "SELECT ticker, id FROM stocks WHERE country = 'KR' AND asset_type = 'etf' AND status = 'active'"
    )
    ids = {str(r[0]): int(r[1]) for r in rs.rows}
    if not ids:
        return []
    if db.usage_blocked_today(client, "krx_openapi"):
        return ["거래소 일일 호출 한도에 도달해 ETF 종가를 받지 않았습니다"]
    result = krx.fetch_etf_daily(bas_dd)
    db.record_api_call(client, "krx_openapi", count=result.attempts, limit_value=krx.DAILY_LIMIT, warn_at_pct=80)
    if not result.ok or not result.data:
        return [f"ETF 종가 수집 실패: {result.error or '빈 응답'} — 다음 실행이 받습니다"]
    now = db.now_iso()
    rows = [
        (ids[row.isu_cd], trade_date, row.open, row.high, row.low, row.close, None,
         row.volume, row.value, "KRW", result.source, now, row.change_pct)
        for row in result.data
        if row.isu_cd in ids and row.close is not None
    ]  # fmt: skip
    db.bulk_upsert_prices(client, rows)
    빠짐 = len(ids) - len(rows)
    return [f"이은 국내 ETF {빠짐}개의 {trade_date} 종가가 응답에 없습니다"] if 빠짐 else []


def _kr_stock_ids(client: TursoClient) -> dict[str, int]:
    # 주식만 — 이은 ETF(25.896)는 `_store_kr_etf_day` 가 따로 받는다. 섞으면 주식 응답에 없다는 이유로 빠진 날
    # 메우기가 ETF 를 찾는다
    rs = client.execute("SELECT ticker, id FROM stocks WHERE country = 'KR' AND asset_type = 'stock'")
    return {str(row[0]): int(row[1]) for row in rs.rows}


def _kr_yahoo_fallback(client: TursoClient, market: str, trade_date: str) -> tuple[int, list[str]]:
    """한국거래소가 실패한 시장의 그날 시세를 야후로 받는다 (docs/data-sources.md 2절, docs/infra.md 25.505).

    **그 거래일 봉만** 넣는다 — 야후가 다른 날을 주면(지연·휴장 착오) 넣지 않는다. 거래대금·등락률·수정종가는
    비운다: 야후는 거래대금을 주지 않고, 추정치를 거래소 실측과 섞으면 유니버스 판정이 흔들린다.
    백필은 한국거래소 행이 있는 날만 채워진 날로 치므로(`backfill_kr.already_stored`) 거래소가 돌아오면 덮인다.
    """
    from batch.sources import yfinance_src

    rs = client.execute(
        "SELECT yahoo_symbol, id FROM stocks"
        " WHERE country = 'KR' AND market = ? AND status = 'active' AND yahoo_symbol IS NOT NULL",
        [market],
    )
    ids = {str(r[0]): int(r[1]) for r in rs.rows}
    # **거래소 값이 이미 있는 종목은 덮지 않는다** (docs/infra.md 25.513, 교차검증). `--force` 재실행에서 이번에만
    # 거래소가 실패하면 앞 실행이 넣은 실측(거래대금·등락률)을 야후 값으로 덮었다
    있음 = {
        int(r[0]) for r in client.execute(
            "SELECT p.stock_id FROM prices p JOIN stocks s ON s.id = p.stock_id"
            " WHERE s.country = 'KR' AND s.market = ? AND p.date = ? AND p.source = ?",
            [market, trade_date, KRX_SOURCE],
        ).rows
    }  # fmt: skip
    ids = {sym: sid for sym, sid in ids.items() if sid not in 있음}
    if not ids:
        if 있음:
            return len(있음), [f"{market} 거래소 실패 — 이미 받아 둔 {trade_date} 거래소 값을 그대로 둡니다"]
        return 0, [f"{market} 야후 폴백: 야후 심볼이 있는 종목이 없습니다"]
    result = yfinance_src.fetch_daily_bars(sorted(ids), lookback_days=5)
    db.record_api_call(client, "yfinance", count=yfinance_src.chunk_count(len(ids)))
    if not result.ok:
        # 이미 거래소 값이 있는 종목은 저장된 것으로 센다 — 0 이면 "한 건도 저장하지 못했습니다" 로 멈췄다 (25.518)
        return len(있음), [f"{market} 야후 폴백도 실패: {result.error or '알 수 없는 실패'}"]
    now = db.now_iso()
    rows = [
        (ids[bar.ticker], trade_date, bar.open, bar.high, bar.low, bar.close, None,
         bar.volume, None, "KRW", result.source, now, None)
        for bar in result.data
        if bar.date == trade_date and bar.ticker in ids and bar.close is not None
    ]  # fmt: skip
    stored = db.bulk_upsert_prices(client, rows)
    return stored, [
        f"{market} 시세를 야후로 대신 받았습니다: {stored:,}/{len(ids):,}종목"
        " (거래대금·등락률 없음 — 그날 기업행위(분할 등) 감지와 단기 신호의 거래대금 배수를 낼 수 없습니다."
        " 다음 거래일 배치가 거래소 값으로 바꿉니다)"
    ]


#: 야후로 대신 받은 날을 거래소 값으로 바꿔 보는 기간(달력일). 긴 연휴(설·추석 5~6일)를 넘기는 길이다 (25.513)
HEAL_DAYS = 10


#: 야후로 받았고 거래소 행이 한 줄도 없는 날 (25.518). 예전 비상관 `NOT IN`(25.525 — 상관 `NOT EXISTS` 는 야후 행마다
#: 그날
#: 전 종목을 훑어 약 1,500배)도 10일치 행 전부(약 5만)를 읽었다. **날짜 색인을 한 칸씩 건너뛰고**, 날마다 그 날의 행만
#: 본다
#: (docs/infra.md 25.865) — 같은 결과에 수십~수천 행
HEAL_DAYS_SQL = (
    "WITH RECURSIVE d(x) AS ("
    " SELECT (SELECT MIN(date) FROM prices WHERE date >= ? AND date < ?)"
    " UNION ALL SELECT (SELECT MIN(date) FROM prices WHERE date > d.x AND date < ?) FROM d WHERE d.x IS NOT NULL) "
    "SELECT x FROM d WHERE x IS NOT NULL"
    " AND NOT EXISTS (SELECT 1 FROM prices k WHERE k.date = d.x AND k.source = ?"
    "   AND k.stock_id IN (SELECT t.id FROM stocks t WHERE t.country = 'KR' AND t.market = ?))"
    " AND EXISTS (SELECT 1 FROM prices p WHERE p.date = d.x AND p.source = ?"
    "   AND p.stock_id IN (SELECT s.id FROM stocks s WHERE s.country = 'KR' AND s.market = ?))"
    " ORDER BY x"
)

#: **다른 국내 시장에는 행이 있는데 이 시장만 한 줄도 없는 날** (docs/infra.md 25.571, 감사). 거래소도 야후 폴백도
#: 실패한 날은 야후 행조차 없어 위 질의에 안 걸려 **영구 구멍**이 됐다 — 다음 날 그 시장 전 종목이 "(정지 뒤 재개)"
#: 가짜 기업행위로 나오고, 20일 평균 거래대금이 그날 0 으로 들어가 하한 근처 종목이 '거래대금미달' 로 빠졌다.
#: 다른 시장에 행이 있다는 것이 그날이 거래일이었다는 근거다
MISSING_MARKET_DAYS_SQL = (
    "WITH RECURSIVE d(x) AS ("
    " SELECT (SELECT MIN(date) FROM prices WHERE date >= ? AND date < ?)"
    " UNION ALL SELECT (SELECT MIN(date) FROM prices WHERE date > d.x AND date < ?) FROM d WHERE d.x IS NOT NULL) "
    "SELECT x FROM d WHERE x IS NOT NULL"
    " AND NOT EXISTS (SELECT 1 FROM prices k WHERE k.date = d.x"
    "   AND k.stock_id IN (SELECT t.id FROM stocks t WHERE t.country = 'KR' AND t.market = ?))"
    " AND EXISTS (SELECT 1 FROM prices p WHERE p.date = d.x"
    "   AND p.stock_id IN (SELECT s.id FROM stocks s WHERE s.country = 'KR' AND s.market <> ?))"
    " ORDER BY x"
)


#: 시세를 한 번도 못 받은 채 주간 유니버스 스냅샷에 이만큼 실렸으면 "못 받음" 을 경고로 세지 않는다
#: (docs/infra.md 25.922). `stocks.fetched_at` 은 주간 마스터 갱신이 덮어 써 나이를 잴 수 없다 —
#: 스냅샷 수가 "적어도 그만큼 주를 지났다" 를 말한다
NEVER_PRICED_QUIET_SNAPSHOTS = 2


def _drop_never_priced(client: TursoClient, note: str, symbols: list[str], bars: list, ids: dict[str, int]) -> str:
    """야후 경고에서 **한 번도 시세가 없던 오래된 종목**의 "못 받음" 을 뺀다 (docs/infra.md 25.922).

    시노백(SVA)은 나스닥 목록에 남아 있지만 몇 년째 거래정지라 야후에 시세가 없다. 그 한 종목 때문에 미국 일일 배치가
    **날마다 partial** 이었다 — partial 이 늘 켜져 있으면 진짜 수집 실패가 묻힌다.
    시세를 한 번이라도 받은 종목이 빠지면 그대로 경고한다.
    새로 들어온 종목(스냅샷 `NEVER_PRICED_QUIET_SNAPSHOTS` 개 미만)도 경고한다. 빼더라도 로그에는 남긴다.
    """
    받음 = {b.ticker for b in bars}
    못받음 = [t for t in symbols if t not in 받음]
    if not 못받음:
        return note
    try:
        조용 = {
            str(r[0]) for r in client.execute(
                # 글자 그대로의 질의 — 스키마 검사가 읽을 수 있게 목록은 JSON 하나로 넘긴다
                "SELECT s.yahoo_symbol FROM stocks s WHERE s.id IN (SELECT value FROM json_each(?))"
                " AND NOT EXISTS (SELECT 1 FROM prices p WHERE p.stock_id = s.id)"
                " AND (SELECT COUNT(*) FROM universe_members u WHERE u.stock_id = s.id) >= ?",
                [json.dumps([ids[t] for t in 못받음]), NEVER_PRICED_QUIET_SNAPSHOTS],
            ).rows
        }  # fmt: skip
    except Exception as exc:  # noqa: BLE001 — 거르지 못하면 예전처럼 다 경고한다
        log.warning("한 번도 시세가 없던 종목을 가리지 못했습니다: %s", exc)
        return note
    if not 조용:
        return note
    log.info("한 번도 시세가 없던 오래된 종목 %d개는 경고에서 뺐습니다: %s", len(조용), ", ".join(sorted(조용)[:10]))
    남은 = [t for t in 못받음 if t not in 조용]
    줄들 = [n for n in note.split("; ") if not n.startswith("시세를 받지 못한 종목")]
    if 남은:
        줄들.append(f"시세를 받지 못한 종목 {len(남은)}개 (예: {', '.join(남은[:5])})")
    return "; ".join(줄들)


def _heal_yahoo_days(client: TursoClient, market: str, trade_date: str, ids: dict[str, int]) -> list[str]:
    """최근 `HEAL_DAYS` 안에 야후로 대신 받은 날을 거래소 값으로 바꾼다 (docs/infra.md 25.513, 교차검증).

    야후 행은 거래대금이 비어 단기 신호의 60일 거래대금 평균이 60거래일 동안 막혔고, 등락률이 비어 그날 분할을
    수정주가가 놓쳤다. "백필이 덮는다" 는 백필이 평소 자동으로 돌지 않아 맞지 않았다. 그래서 거래소가 돌아온
    **다음 일일 배치**가 그날들을 다시 받는다. 실패는 경고로 남기고 다음 날 다시 본다.
    """
    from datetime import date as _date

    from batch.sources import krx, yfinance_src

    시작 = (_date.fromisoformat(trade_date) - timedelta(days=HEAL_DAYS)).isoformat()
    try:
        날들 = [
            str(r[0]) for r in client.execute(
                # **거래소 행이 한 줄도 없는 날만** (25.518, 교차검증). "야후 행이 하나라도" 로 고르면 거래소가 종가를
                # 주지 않는 종목(거래정지)의 야후 행 때문에 같은 날을 열흘 동안 매일 다시 받았다
                HEAL_DAYS_SQL,
                [시작, trade_date, trade_date, KRX_SOURCE, market, yfinance_src.SOURCE, market],  # 25.868
            ).rows
        ]  # fmt: skip
        날들 = sorted(set(날들) | {
            str(r[0]) for r in client.execute(
                MISSING_MARKET_DAYS_SQL, [시작, trade_date, trade_date, market, market]
            ).rows
        })  # fmt: skip
    except Exception as exc:  # noqa: BLE001 — 바꾸지 못해도 오늘 시세는 저장됐다
        return [f"{market} 야후로 받은 날을 찾지 못했습니다: {exc}"]
    out: list[str] = []
    잇단실패 = 0
    for 날 in 날들:
        # **부르기 전에 보고, 막히거나 잇달아 실패하면 멈춘다** (docs/infra.md 25.606, 감사). 최근 10일의 야후 날을
        # 하루씩 부르는데, 한도가 찼거나 거래소가 죽은 날에도 날마다 불러 같은 경고를 쌓았다
        if db.usage_blocked_today(client, "krx_openapi"):
            out.append(f"{market} 야후로 받은 날을 거래소 한도로 다시 받지 못했습니다 — 다음 배치가 다시 봅니다")
            break
        result = krx.fetch_daily(market, 날.replace("-", ""))
        db.record_api_call(client, "krx_openapi", count=result.attempts, limit_value=krx.DAILY_LIMIT, warn_at_pct=80)
        if not result.ok or not result.data:
            out.append(f"{market} {날} 시세를 거래소 값으로 채우지 못했습니다 — 다음 배치가 다시 봅니다")
            잇단실패 += 1
            if result.limit_state == "blocked" or 잇단실패 >= 2:
                break
            continue
        잇단실패 = 0
        _store_krx_day(client, result.data, 날, ids, result.source)
        # 바꾼 것은 경고가 아니다 — 경고로 넣으면 리포트가 그날 "경고" 로 나갔다 (25.518, 교차검증)
        log.info("%s %s 야후로 받았던 시세를 거래소 값으로 바꿨습니다", market, 날)
        # **그날의 기업행위도 본다** (25.518, 교차검증). 야후로 받은 날은 등락률이 없어 분할 감지를 건너뛰었고,
        # 다시 받은 뒤에도 감지는 "오늘" 만 봐서 그날 분할이 끝내 알려지지 않았다
        try:
            out.extend(_detect_kr_actions(client, 날, result.data, ids))
        except Exception as exc:  # noqa: BLE001 — 알리는 길이다. 실패는 경고로
            out.append(f"{market} {날} 기업행위(분할 등) 확인을 하지 못했습니다: {exc}")
    return out


def _store_krx_day(
    client: TursoClient,
    krx_rows: list,
    trade_date: str,
    ids: dict[str, int],
    source: str,
) -> int:
    now = db.now_iso()
    rows_data: list[tuple] = []

    for row in krx_rows:
        stock_id = ids.get(row.isu_cd)
        if stock_id is None or row.close is None:
            continue
        rows_data.append(
            (
                stock_id, trade_date, row.open, row.high, row.low, row.close,
                # 한국거래소 원자료는 미조정이다. adj_close 는 jobs/adjust_kr 이 등락률로
                # 되살려 채운다 (docs/adjust.md). 여기서는 받은 값만 저장한다
                None,
                row.volume, row.value, "KRW", source, now, row.change_pct,
            )
        )

    return db.bulk_upsert_prices(client, rows_data)


def _report_rows_from_db(
    client: TursoClient, trade_date: str, targets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """저장된 값으로 리포트 행을 만든다. 저장하지 않은 숫자는 싣지 않는다."""
    rows: list[dict[str, Any]] = []

    for target in targets:
        rs = client.execute(
            "SELECT p.close, p.adj_close, p.change_pct, p.volume, p.currency, p.source, p.date"
            " FROM prices p JOIN stocks s ON s.id = p.stock_id"
            " WHERE s.ticker = ? AND s.country = ? AND p.date <= ?"
            " ORDER BY p.date DESC LIMIT 2",
            [target["ticker"], target["country"], trade_date],
        )
        found = rs.dicts()
        if not found:
            continue

        latest = found[0]
        # **등락률은 저장된 값을 먼저 쓴다** (docs/infra.md 25.326). 거래소가 준 `change_pct` 는 기업행위를 반영한
        # 기준가로 낸 값이다. 예전에는 두 행의 원 종가로 다시 계산해 **액면분할 다음 날 −98%** 같은 가짜 등락이 나갔다.
        # 저장값이 없으면(미국 등) 수정종가로 계산한다 — 분할이 섞이지 않는다
        change = latest.get("change_pct")
        if change is None and len(found) > 1:
            지금 = latest.get("adj_close") or latest["close"]
            전 = found[1].get("adj_close") or found[1]["close"]
            change = (지금 - 전) / 전 * 100 if 전 not in (None, 0) else None
        rows.append(
            {
                "name": target["name_ko"] or target["name_en"] or target["ticker"],
                "close": latest["close"],
                "change_pct": change,
                "volume": latest["volume"],
                "currency": latest["currency"],
                "source": latest["source"],
                "trade_date": latest["date"],
                "settled": True,
            }
        )
    return rows


def refresh_recommendations(market: str, trade_date: str) -> list[str]:
    """점수와 신호를 그 거래일 기준으로 다시 낸다. 실패는 경고로 돌려준다.

    실패해도 리포트는 보낸다. 시세는 이미 받았고, 추천은 마지막으로 계산된
    신호로 싣되 그 기준일을 리포트에 적는다. 조용히 옛 추천을 새것처럼 보이게
    하지 않는 것이 핵심이다.

    각 배치는 자기 batch_runs 행을 따로 남긴다. 어디서 깨졌는지 따로 보인다.
    """
    from batch.jobs import scores, sentiment, signals

    warnings: list[str] = []
    # 감성을 점수보다 먼저 낸다. 종합 점수가 최근 3일 안의 감성을 읽는다 (docs/sentiment.md)
    code, 예외 = _하위작업(lambda: sentiment.run(market, as_of=trade_date))
    if 예외:
        # 실제로는 3일 안의 앞선 감성 행이 있으면 그것을 쓴다(`scores.load_sentiments`) — "감성 없이" 는
        # 틀린 말이었다 (25.837, 감사)
        warnings.append(f"{_실패문('뉴스 감성 집계', 예외)}. 3일 안의 앞선 감성이 있으면 그것으로 점수를 냅니다")
    elif code != 0:
        warnings.append(
            "뉴스 감성 집계 실패 (sentiment 배치 기록 참고). 3일 안의 앞선 감성이 있으면 그것으로 점수를 냅니다"
        )

    # **단계마다 제 실패를 말한다** (docs/infra.md 25.372). 예전에는 `if not warnings:` 로 "앞에 경고가 없을 때만"
    # 점수·신호 실패를 적어, 감성 경고가 먼저 있으면 **점수를 못 냈다는 사실이 리포트에서 빠졌다**
    code, 예외 = _하위작업(lambda: scores.run(market, as_of=trade_date))
    if code != 0:
        warnings.append(_실패문("점수 계산", 예외) if 예외 else "점수 계산 실패 (scores 배치 기록 참고)")
        return warnings

    시작 = db.now_iso()
    code, 예외 = _하위작업(lambda: signals.run(market, as_of=trade_date))
    if code != 0:
        warnings.append(_실패문("신호 계산", 예외) if 예외 else "신호 계산 실패 (signals 배치 기록 참고)")
    else:
        # **건너뛴 계산도 말한다** (docs/infra.md 25.547, 교차검증). 기준일 종가가 없는 종목이 많아 신호를 계산하지 않은
        # 날(25.526·25.541) `run` 은 0 을 돌려줘, 리포트가 까닭 없이 "오늘 계산분 없음" 만 적었다
        건너뜀 = _건너뛴_까닭(signals.JOB_NAME, 시작)
        if 건너뜀:
            warnings.append(건너뜀)
    return warnings


def _건너뛴_까닭(job_name: str, since: str) -> str | None:
    """`since` 뒤에 연 그 작업의 실행이 `skipped` 면 리포트 경고 한 줄. 못 읽으면 못 읽었다고 말한다."""
    try:
        with TursoClient() as client:
            rs = client.execute(
                "SELECT status, step_log FROM batch_runs WHERE job_name = ? AND started_at >= ?"
                " ORDER BY id DESC LIMIT 1",
                [job_name, since],
            ).rows
    except Exception as exc:  # noqa: BLE001 — 말 한 줄 때문에 리포트를 막지 않는다
        return f"신호를 계산했는지 읽지 못했습니다: {exc}"
    if not rs or str(rs[0][0]) != "skipped":
        return None
    try:
        까닭 = str((json.loads(rs[0][1] or "{}") or {}).get("reason") or "까닭 기록 없음")
    except (TypeError, ValueError):
        까닭 = "까닭 기록 없음"
    # "어제" 라 적지 않는다 — 월요일·연휴 뒤에는 틀린 말이다. 기준일은 추천 머리가 적는다 (25.821, 리포트 감사)
    return f"신호를 계산하지 않았습니다 — {까닭}. 앞서 계산된 신호를 그대로 씁니다(기준일은 추천 머리에)"


def _실패문(이름: str, exc: BaseException) -> str:
    """하위 작업이 예외로 끝난 것을 리포트 한 줄로. **한도는 "실패" 가 아니라 "건너뜀"** 이다 (docs/infra.md 25.666,
    교차검증) — 기록은 `skipped` 로 닫는데(25.664) 리포트는 "…실패: D1_ERROR …" 라 적어 둘이 어긋났다."""
    한도 = db.quota_reason(exc)
    return f"{이름} 건너뜀 (DB 한도): {한도}" if 한도 else f"{이름} 실패: {exc}"


def _하위작업(job: Callable[[], int]) -> tuple[int, Exception | None]:
    """하위 작업을 부른다. 예외는 삼키되 **그 작업이 연 실행 기록은 실패로 닫는다** (docs/infra.md 25.372)."""
    depth = db.open_run_depth()
    try:
        return job(), None
    except Exception as exc:  # noqa: BLE001 — 하위 작업 실패로 리포트를 멈추지 않는다
        log.exception("하위 작업 실패")
        # 한도는 고장이 아니다 — `skipped` 로 닫는다 (docs/infra.md 25.6·25.664, 감사). 예전에는 `failed` 였다
        한도 = db.quota_reason(exc)
        if 한도:
            db.fail_runs_opened_after(depth, f"DB 한도로 건너뜀: {한도}", "skipped")
        else:
            db.fail_runs_opened_after(depth, str(exc))
        return 1, exc


def refresh_portfolio(market: str | None = None) -> list[str]:
    """보유 평가를 그날 종가로 다시 내고 매도 플래그를 판정한다 (docs/portfolio.md, docs/sell_flags.md).

    매매가 없으면 빈 표로 끝난다. 실패는 경고로 돌려주고 리포트는 보낸다.
    """
    from batch.jobs import monitor_targets, portfolio, sell_flags

    steps = [("포트폴리오 재계산", portfolio.run), ("매도 플래그", sell_flags.run)]
    if market:
        # 장중 감시 대상과 세션은 보유·신호가 확정된 뒤에 만든다 (docs/intraday.md)
        steps.append(("장중 감시 준비", lambda: monitor_targets.run(market)))
    if market == "KR":
        # 지켜보는 종목의 공시 목록. 감시 대상이 정해진 뒤라야 누구 것을 받을지 안다 (docs/data-sources.md 1.2)
        from batch.jobs import disclosures_kr

        steps.append(("공시 수집", disclosures_kr.run))
    # 이 시각 뒤에 열린 기록만 읽는다 — 하위 작업이 기록을 열기 전에 죽으면 옛 기록을 오늘 것으로 싣는다 (25.496)
    시작 = db.now_iso()
    # **환율을 먼저 받는다** (docs/infra.md 25.137). 바로 아래 보유 평가의 재료다
    warnings: list[str] = list(_collect_fx())
    for name, job in steps:
        code, exc = _하위작업(job)
        if exc is not None:
            warnings.append(_실패문(name, exc))
            continue
        if code != 0:
            warnings.append(f"{name} 실패 (배치 기록 참고)")
    # **하위 작업의 "모름" 경고를 리포트로 올린다** (docs/infra.md 25.492, 텔레그램 감사).
    # 두 작업은 경고가 있어도 0 을 돌려주므로 예전에는 아무 말도 실리지 않았다 — 종가·환율 묵음,
    # 감성을 못 읽어 감성급락 판정 생략, 수수료·세율 미설정 등이 텔레그램에서 "플래그 없음" 과 똑같이 보였다
    하위 = [portfolio.JOB_NAME, sell_flags.JOB_NAME]
    if market == "KR":
        # 공시 수집도 경고가 있어도 0 을 돌려준다 — 전부 실패해도 리포트에 아무 말이 없어 장중 공시 알림이 조용히
        # 멈췄다 (docs/infra.md 25.605, 감사)
        하위.append(disclosures_kr.JOB_NAME)
    warnings.extend(_하위_경고(하위, 시작))
    return warnings


#: 하위 작업 경고를 리포트에 올릴 때 작업마다 최대 줄 수 — 리포트가 경고로 덮이지 않게 (25.492)
하위_경고_최대 = 3


def _하위_경고(job_names: list[str], since: str) -> list[str]:
    """방금 돈 하위 작업들의 마지막 기록에 남은 경고. 못 읽으면 빈 목록(리포트는 보낸다).

    `since` 전에 열린 기록은 읽지 않는다 (docs/infra.md 25.496, 교차검증). 작업이 기록을 열기 전에 죽으면
    (클라이언트·마이그레이션 실패) 어제 기록의 경고가 날짜 없이 오늘 경고로 실렸다.
    건수는 `warning_count` 를 쓴다 — 기록에는 앞 몇 건만 남아 "외 N건" 이 실제보다 작았다.
    """
    나온것: list[str] = []
    try:
        with TursoClient() as client:
            for job_name in job_names:
                row = client.execute(
                    "SELECT step_log FROM batch_runs WHERE job_name = ? AND started_at >= ? ORDER BY id DESC LIMIT 1",
                    [job_name, since],
                ).rows
                if not row:
                    continue
                기록 = json.loads(row[0][0] or "{}") or {}
                경고 = 기록.get("warnings") or []
                전체 = max(int(기록.get("warning_count") or 0), len(경고))
                for w in 경고[:하위_경고_최대]:
                    나온것.append(f"{job_name}: {w}")
                if 전체 > 하위_경고_최대:
                    나온것.append(f"{job_name}: … 외 {전체 - 하위_경고_최대}건 (배치 기록 참고)")
    except Exception as exc:  # noqa: BLE001 — 경고를 못 읽어도 리포트는 나간다. 못 읽었다는 것은 말한다
        나온것.append(f"하위 작업 경고를 읽지 못했습니다: {exc}")
    return 나온것


def load_holdings(
    client: TursoClient, currency: str, fx_rate: float | None, warnings: list[str] | None = None
) -> list[report_picks.Holding]:
    """현재 보유를 리포트 통화로. 원화 풀 하나라 **모든 나라의 보유**가 여력을 줄인다 (docs/design.md 3.9 2부).

    평가는 portfolio 배치가 낸 positions.market_value_krw 그대로다. 미국 리포트(달러)는 그날 환율로 나눈다.
    평가되지 않은 보유(종가·환율 없음)는 뺀다 — 값을 지어내지 않는다. 표가 없으면 빈 목록.

    **표가 없는 것 말고 읽기가 실패하면 `warnings` 에 적는다** (docs/infra.md 25.219). 예전에는 아무 실패나
    "보유 없음" 이 되어, 2부가 보유를 빼고 여력·업종 상한·상관을 계산해 **권장 금액이 실제보다 커졌는데**
    리포트는 아무 말이 없었다(D1 한도·인증 끊김도 같은 빈 값이 된다).
    """
    try:
        rs = client.execute(
            "SELECT p.stock_id, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.sector, s.country,"
            "       p.market_value_krw, p.market_value, p.currency AS pos_currency, s.asset_type"
            " FROM positions p JOIN stocks s ON s.id = p.stock_id"
            " WHERE p.quantity > 0"
        )
    except Exception as exc:  # noqa: BLE001 — 포트폴리오 표가 아직 없는 DB 는 정상
        if not db.표가_없나(exc):
            log.warning("보유를 읽지 못했습니다: %s", exc)
            if warnings is not None:
                warnings.append(
                    "보유를 읽지 못해 2부의 여력·업종 상한·상관을 **보유 없이** 계산했습니다"
                    f" — 금액이 실제보다 클 수 있습니다 ({exc})"
                )
        return []
    # 재계산이 쓰다 말았으면 보유가 일부만 있다 — 여력이 부풀었을 수 있다고 말한다 (25.648, 교차검증)
    from batch.jobs import portfolio as _pf

    if warnings is not None and _pf.쓰는_중(client):
        warnings.append(
            "포트폴리오 재계산이 끝나지 않아 보유가 일부만 읽혔을 수 있습니다 — 2부 금액이 실제보다 클 수 있습니다"
        )
    out: list[report_picks.Holding] = []
    빠진: list[str] = []
    환율없어_뺀 = 0
    for row in rs.dicts():
        # **평가 못 한 보유는 빼되 말한다** (docs/infra.md 25.411). 예전에는 질의에서 조용히 걸렀다 —
        # 환율이 없어 미국 보유 전부가 비면 국내 2부의 여력이 그만큼 부풀었고, 그 종목이 다시 추천되면
        # 종목 상한을 보유 0 으로 계산했는데 리포트는 아무 말이 없었다. 값은 지어내지 않는다
        if currency != "KRW" and not (fx_rate and fx_rate > 0):
            # 환율이 없으면 먼저 센다 — 그날은 금액 자체를 내지 않으므로 "실제보다 클 수 있다" 는 맞지 않다 (25.441)
            환율없어_뺀 += 1
            continue
        if row["market_value_krw"] is None:
            빠진.append(str(row["ticker"]))
            continue
        value_krw = float(row["market_value_krw"])
        if currency == "KRW":
            value = value_krw
        elif row.get("pos_currency") == currency and row.get("market_value") is not None:
            # **같은 통화 보유는 종목 통화 평가액 그대로** (docs/infra.md 25.494, 텔레그램 감사).
            # 원화 평가액은 포트폴리오 배치가 표의 가장 새 환율(당일 진행 중 봉일 수 있다)로 곱한 값이고,
            # 여기서 나누는 환율은 리포트 기준일(D-1) 환율이다 — 둘로 오가면 달러 평가액이
            # (당일 ÷ 전일 환율) 만큼 틀어져 여력·종목 상한이 어긋났다
            value = float(row["market_value"])
        elif fx_rate and fx_rate > 0:
            value = value_krw / fx_rate
        else:
            환율없어_뺀 += 1
            continue  # 환율이 없으면 달러 리포트에 원화 보유를 섞지 않는다
        out.append(
            report_picks.Holding(
                int(row["stock_id"]), str(row["ticker"]), str(row["name"]), row["sector"], value,
                country=row.get("country"), asset_type=str(row.get("asset_type") or "stock"),
            )
        )
    # **환율이 없어 뺀 것은 따로 말한다** (docs/infra.md 25.426, 교차검증 지적). 그때는 미국 권장 금액 자체가 나오지
    # 않으므로(daily 가 이미 경고한다) "금액이 실제보다 클 수 있습니다" 는 맞지 않는 말이었고, 국내 보유까지
    # "평가하지 못한 보유" 로 나열됐다
    if 환율없어_뺀 and warnings is not None:
        warnings.append(f"환율이 없어 보유 {환율없어_뺀}종목을 달러 리포트의 여력 계산에 넣지 못했습니다")
    if 빠진 and warnings is not None:
        warnings.append(
            f"평가하지 못한 보유 {len(빠진)}종목({', '.join(빠진[:5])}{' 등' if len(빠진) > 5 else ''})은"
            " 2부의 여력·종목 상한·업종 상한에서 빠졌습니다(종가 또는 환율 없음) — 금액이 실제보다 클 수 있습니다"
        )
    return out


#: 상관 창을 달력 날짜로 옮길 때 쓰는 여유. 126 거래일 ≈ 176 달력일이고, 휴장과
#: 수집 구멍을 보태 넉넉히 잡는다. **넉넉한 쪽이 안전하다** — 모자라면 겹치는 날이
#: `CORR_MIN_POINTS` 아래로 떨어져 상관을 아예 못 낸다(그러면 안 줄인다)
CORR_CALENDAR_SLACK_DAYS = 30
#: 한 질의에 넣을 종목 수. D1 은 질의당 파라미터 100개까지다(core/d1.MAX_PARAMS).
#: 날짜 인자 둘을 빼고 90 으로 둔다 — 보유가 100종목을 넘어도 조용히 반쪽만 읽지 않는다
CORR_IDS_PER_QUERY = 90


def load_price_series(
    client: TursoClient, stock_ids: list[int], as_of: str, warnings: list[str] | None = None
) -> dict[int, dict[str, float]]:
    """상관을 내려고 읽는 `{종목번호: {날짜: 수정종가}}` (docs/signals.md 3.6).

    **후보와 보유를 한꺼번에** 읽는다. 둘은 나라가 다를 수 있다 — 원화 풀 하나라
    미국 보유도 국내 리포트의 여력을 줄이기 때문이다(`load_holdings`).

    창은 **달력 날짜**로 자른다. 거래일로 세지 않는 이유는 나라마다 거래일이 달라
    "126 거래일" 이 종목마다 다른 구간을 뜻하기 때문이다. 어차피
    `report_picks._aligned_returns` 가 **겹치는 날만** 다시 골라낸다.

    한 가지 알고 쓴다: **나라가 다르면 같은 날짜의 종가가 같은 시각이 아니다.**
    한국 종가는 미국 장이 열리기 전이다. 그래서 국내–미국 상관은 실제보다 **낮게**
    나온다. 낮게 나오면 덜 줄이므로 **안전한 쪽으로 틀린다** — 지어내는 것보다 낫다.
    `[확인필요: 하루 밀어 맞추는 편이 나은지]`
    """
    if not stock_ids:
        return {}
    from datetime import date, timedelta

    from batch.services.report_picks import CORR_WINDOW

    가로 = int(CORR_WINDOW * 7 / 5) + CORR_CALENDAR_SLACK_DAYS
    since = (date.fromisoformat(as_of) - timedelta(days=가로)).isoformat()

    out: dict[int, dict[str, float]] = {}
    고유 = sorted(set(stock_ids))
    for start in range(0, len(고유), CORR_IDS_PER_QUERY):
        묶음 = 고유[start : start + CORR_IDS_PER_QUERY]
        자리 = ",".join("?" for _ in 묶음)
        try:
            rs = client.execute(
                "SELECT stock_id, date, COALESCE(adj_close, close) AS px FROM prices"
                f" WHERE stock_id IN ({자리}) AND date >= ? AND date <= ? AND close IS NOT NULL"
                " ORDER BY stock_id, date",
                [*묶음, since, as_of],
            )
        except Exception as exc:  # noqa: BLE001 — 시세를 못 읽으면 상관 없이 간다(안 줄인다)
            log.exception("상관용 시세 읽기 실패 — 상관으로 줄이지 않습니다")
            # **리포트에도 말한다** (docs/infra.md 25.220). 안 줄인 금액은 **실제보다 크다**
            if warnings is not None:
                warnings.append(f"상관용 시세를 읽지 못해 2부 금액을 상관으로 줄이지 않았습니다 ({exc})")
            return {}
        for row in rs.dicts():
            out.setdefault(int(row["stock_id"]), {})[str(row["date"])] = float(row["px"])
    return out


#: 한국거래소 시세의 출처 표기 (`sources.krx.SOURCE`). 폴백이 덮지 않을 행을 가른다
KRX_SOURCE = "krx_openapi"

#: 일부 발송 경고의 표지 — 아래 발송 경고 문구 안의 글자와 같아야 한다(웹 `PARTIAL_SEND_MARK` 도 같다)
PARTIAL_SEND_MARK = "조각만 보냈습니다"

#: 다른 나라 리포트의 배분을 여력에서 빼는 기간. 국내 08:30 과 미국 22:30(서머타임 21:30) KST 는 서로 10~14시간
#: 떨어져 있다 — 바로 앞 리포트 하나만 잡고 그 전날 것은 잡지 않는 길이다 (docs/infra.md 25.508)
다른_리포트_시간 = 20


#: 다른 나라 배분을 여력에서 빼지 못했을 때 붙이는 말 (25.646)
_넘칠수 = " — 두 리포트 합이 예산을 넘을 수 있습니다"


def _other_report_reserved(
    client: TursoClient, market: str, currency: str, fx_rate: float | None, holdings: list,
    trade_date: str | None = None,
) -> tuple[float, str | None]:
    """다른 나라 리포트가 최근 `다른_리포트_시간` 안에 2부에서 배분한 금액(이 리포트 통화)과 한 줄 (infra 25.508).

    **원화 풀은 하나다.** 예전에는 국내·미국 리포트가 같은 남은 여력을 각자 전부 썼다 — 두 리포트대로 사면 여력보다
    더 썼다(감사 재현: 여력 4,000만에 3,000만 + 3,000만). 아직 안 샀어도 뺀다(보수적인 쪽: 돈을 덜 쓴다).
    이미 보유한 종목의 배분도 뺀다(25.515). 못 읽으면 0 이고 로그를 남긴다.
    """
    다른 = "US" if market == "KR" else "KR"
    기준 = (datetime.now(UTC) - timedelta(hours=다른_리포트_시간)).isoformat()
    try:
        # **이 나라의 앞 리포트 뒤에 나온 다른 나라 리포트**도 잡는다 (docs/infra.md 25.515, 교차검증). 20시간만 보면
        # 월요일 국내 리포트가 금요일 밤 미국 리포트(약 59시간 전)를 못 잡았다 — 연휴 뒤도 같다
        rows = client.execute(
            "SELECT i.stock_id, i.payload_json FROM report_items i"
            " WHERE i.section = ? AND i.report_id = ("
            "   SELECT r.id FROM daily_reports r WHERE r.market = ? AND r.generated_at >= MIN(?, COALESCE("
            # 같은 거래일 리포트는 "앞 리포트" 가 아니다 — `--force` 재실행이 아침 것을 잡아 경계가 20시간으로
            # 돌아갔다(25.520, 교차검증). 여력은 이번 리포트를 저장하기 전에 센다
            "     (SELECT MAX(x.generated_at) FROM daily_reports x WHERE x.market = ? AND x.generated_at < ?"
            "        AND x.trade_date < COALESCE(?, x.trade_date || 'z')), ?))"
            "   ORDER BY r.generated_at DESC LIMIT 1)",
            [reports.SECTION_BUY, 다른, 기준, market, datetime.now(UTC).isoformat(), trade_date, 기준],
        ).rows
    except Exception as exc:  # noqa: BLE001 — 못 읽으면 빼지 않는다(예전과 같다). 조용히 넘기지 않는다
        if not db.표가_없나(exc):
            log.warning("다른 나라 리포트 배분을 읽지 못해 여력에서 빼지 않습니다: %s", exc)
            # 로그만 남기면 리포트는 여력 전체를 쓰고 아무 말이 없다 — 예산을 넘을 수 있다고 적는다 (25.646, 감사)
            이름 = "미국" if 다른 == "US" else "국내"
            return 0.0, f"{이름} 리포트 2부 배분을 읽지 못해 여력에서 빼지 않았습니다{_넘칠수}"
        return 0.0, None
    if (not fx_rate or fx_rate <= 0) and rows:
        # 국내 리포트는 환율을 읽지 않는다 — 달러 배분을 원화로 바꾸려면 여기서 읽는다
        from batch.services import fx as fx_svc

        try:
            최근 = fx_svc.latest_rate(client, datetime.now(UTC).date().isoformat())
            fx_rate = 최근.rate if 최근 is not None else None
        except Exception as exc:  # noqa: BLE001 — 환율을 못 읽으면 다른 통화 배분은 빼지 않는다
            log.warning("다른 나라 리포트 배분을 환산할 환율을 읽지 못했습니다: %s", exc)
    합 = 0.0
    못뺀: dict[str, float] = {}  # 환율이 없어 환산하지 못한 배분(통화별) — 말없이 넘기지 않는다 (25.646, 감사)
    환산함 = False
    # 이미 보유한 종목도 뺀다 (25.515, 교차검증) — 보유 종목 상한까지 채우는 추가 배분이 여력에서 빠지지 않았다.
    # 그 배분대로 이미 샀다면 보유와 두 번 세지만, 다음 리포트 주기까지이고 돈을 덜 쓰는 쪽이다
    # (`holdings` 는 보지 않는다)
    for _stock_id, payload in rows:
        try:
            p = json.loads(payload or "{}")
            금액, 통화 = float(p.get("amount") or 0), str(p.get("currency") or ("KRW" if 다른 == "KR" else "USD"))
        except (TypeError, ValueError):
            continue
        if 통화 == currency:
            합 += 금액
        elif not fx_rate or fx_rate <= 0:
            못뺀[통화] = 못뺀.get(통화, 0.0) + 금액  # 환율이 없으면 환산할 수 없다 — 빼지 않고 말한다
            continue
        elif 통화 == "USD" and currency == "KRW":
            합 += 금액 * fx_rate
            환산함 = True
        elif 통화 == "KRW" and currency == "USD":
            합 += 금액 / fx_rate
            환산함 = True
    이름 = "미국" if 다른 == "US" else "국내"
    말: list[str] = []
    if 합 > 0:
        환산 = f" (환율 {fx_rate:,.2f})" if 환산함 and fx_rate else ""
        말.append(f"{이름} 리포트 2부 배분 {report_sections._money(합, currency)}만큼 여력을 줄였습니다{환산}")
    if 못뺀:
        금액들 = ", ".join(report_sections._money(v, k) for k, v in sorted(못뺀.items()))
        말.append(f"{이름} 리포트 2부 배분 {금액들}은 환율이 없어 여력에서 빼지 못했습니다{_넘칠수}")
    return 합, (" · ".join(말) if 말 else None)


def _last_report_sent_at(client: TursoClient, country: str) -> str | None:
    """이 나라 리포트를 마지막으로 보낸 시각(`sent_at`). 모르면 None (docs/infra.md 25.495·25.501).

    매도 플래그의 NEW 는 "이 리포트가 아직 보여 주지 않은 것" 이다 — 그 경계가 이 시각이다. 날짜로 견주면
    아침 발송 뒤 같은 날 저녁 미국 배치에서 처음 걸린 국내 플래그가 다음 날 NEW 를 못 받았다(25.501, 교차검증).
    오늘 이미 보낸 리포트도 센다 — 같은 날 다시 돌리면(`--force`) 아침에 보여 준 것은 NEW 가 아니다.
    """
    try:
        rows = client.execute(
            # **일부만 나간 리포트는 보여 준 것으로 치지 않는다** (docs/infra.md 25.512, 교차검증).
            # 매도 플래그는 글 끝에 붙어 잘린 뒷부분에 있었을 가능성이 크다 —
            # 그런데도 발송 시각이 찍혀 다음 날 NEW 가 빠졌다
            "SELECT sent_at FROM daily_reports WHERE market = ? AND sent_at IS NOT NULL"
            " AND instr(COALESCE(warnings_json, ''), ?) = 0 ORDER BY sent_at DESC LIMIT 1",
            [country, PARTIAL_SEND_MARK],
        ).rows
    except Exception as exc:  # noqa: BLE001 — 모르면 예전 규칙(처음 걸린 날 == 오늘)으로 간다
        if not db.표가_없나(exc):
            # 조용히 넘기지 않는다 — NEW 가 예전 규칙으로 돌아간 것을 로그에 남긴다
            log.warning("마지막 리포트 발송 시각을 읽지 못해 매도 플래그 NEW 를 예전 규칙으로 판정합니다: %s", exc)
        return None
    return str(rows[0][0]) if rows else None


def _sell_flags_for_report(client: TursoClient, country: str, warnings: list[str] | None = None) -> list[dict]:
    from batch.jobs import sell_flags

    try:
        # `sell_flags.run` 이 찍은 `first_seen_date` 와 **같은 달력**이어야 "새로 걸림" 이 맞는다
        # (docs/infra.md 25.125). 둘이 UTC 였을 때는 배치가 자정 UTC(09:00 KST)를 넘기면 어긋났다
        오늘 = cal.user_today().isoformat()
        return sell_flags.active_flags_for_report(client, country, 오늘, _last_report_sent_at(client, country))
    except Exception as exc:  # noqa: BLE001 — 표가 아직 없으면 플래그 없이 보낸다
        log.exception("매도 플래그 읽기 실패")
        # 표가 없는 것 말고는 **리포트에 말한다** (docs/infra.md 25.220).
        # 조용히 빠지면 "오늘 플래그 없음" 과 같아 보인다
        if warnings is not None and not db.표가_없나(exc):
            warnings.append(f"매도 플래그를 읽지 못해 리포트에 싣지 못했습니다 — 화면에서 확인하세요 ({exc})")
        return []


def load_signal_rows(
    client: TursoClient, country: str, upto: str | None = None
) -> tuple[list[report_picks.SignalRow], str | None]:
    """그 나라의 가장 최근 신호와 점수·성과·종가. 웹 추천 화면과 같은 행을 읽는다.

    `upto` 를 주면 **그 날짜보다 뒤의 신호는 보지 않는다** (docs/infra.md 25.234). 2026-09-26 까지 따로 도는
    신호 계산이 UTC 오늘(토·일 포함)로 기록해, 월요일 리포트가 금요일 기준 계산보다 **일요일 날짜 행**을 가장
    새 것으로 읽었다. 기본값은 고쳤지만 이미 쌓인 행이 남아 있을 수 있다 — 리포트의 거래일보다 뒤는 없는 것이다.
    """
    as_of = db.last_signal_calc_date(client, country, upto)
    if not as_of:
        return [], None

    rs = client.execute(
        "SELECT s.id AS stock_id, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.market, s.sector,"
        "  sg.horizon, sg.signal_type, sg.currency, sg.buy_zone_low, sg.buy_zone_high,"
        "  sg.suggested_weight_pct, sg.suggested_amount, sg.size_reduction, sg.tranche_plan,"
        "  sg.rationale_text, sg.as_of_date, sg.rationale_data,"
        "  sc.total_score, sc.rank_in_market, sc.factor_scores, sc.sentiment_score,"
        "  (SELECT p.close FROM prices p WHERE p.stock_id = s.id AND p.date <= sg.as_of_date"
        # 성과 지표(CAGR·MDD·샤프)는 아래에서 신호와 같은 규칙으로 따로 고른다 (docs/infra.md 25.268)
        "    ORDER BY p.date DESC LIMIT 1) AS close,"
        # 그 종가의 날짜 — 리포트가 "현재가" 가 아니라 "몇 일 종가" 로 적는다 (docs/infra.md 25.348)
        "  (SELECT p.date FROM prices p WHERE p.stock_id = s.id AND p.date <= sg.as_of_date"
        "    AND p.close IS NOT NULL ORDER BY p.date DESC LIMIT 1) AS close_date"
        " FROM signals sg"
        " JOIN stocks s ON s.id = sg.stock_id"
        # **계산 판(calc_version)까지 봐야 한 행이다** (2026-09-21, docs/infra.md 25.91).
        # `scores` 의 유니크 키는 (stock_id, as_of_date, **calc_version**) 이고 적재는
        # **같은 판만 지운다**(jobs/scores.clear_statement). 그래서 계산식을 올린 날
        # (CALC_VERSION 3→4)에는 같은 기준일에 두 행이 남고, 이 조인이 한 종목을 **두 번**
        # 돌려줬다. 날짜 고르는 규칙은 그대로 두고 **판만 한 겹 더** 건다 —
        # 그래야 각 자리가 원래 보던 날짜 규칙을 잃지 않는다
        " LEFT JOIN scores sc ON sc.stock_id = s.id"
        "  AND sc.as_of_date = (SELECT MAX(as_of_date) FROM scores"
        "    WHERE stock_id = s.id AND as_of_date <= sg.as_of_date)"
        "  AND sc.calc_version = (SELECT MAX(c.calc_version) FROM scores c"
        "                         WHERE c.stock_id = sc.stock_id AND c.as_of_date = sc.as_of_date)"
        # 상장 상태 조건을 웹 카드(`recommend.buildQuery`)와 같게 — 신호 뒤 제외·폐지된 종목이 리포트에만 실리지 않게
        # (25.802)
        " WHERE s.country = ? AND sg.as_of_date = ? AND s.status = 'active'"
        # **신호도 판까지 봐야 한 행이다 — 그 나라·그 기준일의 가장 새 판 하나**
        # (docs/infra.md 25.410·25.423, 웹 `lib/recommend` 와 같은 조건).
        # 판을 올린 뒤 같은 기준일을 다시 계산하면 옛 판 행이 남아(적재는 같은 판만 지운다) 1부에 같은 기간이
        # 두 번 실리고, 2부는 둘 중 큰 금액 — 옛 판 — 을 골랐다
        "   AND sg.calc_version = (SELECT MAX(c.calc_version) FROM signals c JOIN stocks s3 ON s3.id = c.stock_id"
        # 바깥 행을 가리키지 않는다 — 가리키면 신호 행마다 다시 돌아 읽는 행이 수백 배가 됐다 (25.428)
        "     WHERE s3.country = ? AND c.as_of_date = ?)",
        [country, as_of, country, as_of],
    )
    # **과거 성과(CAGR·MDD·샤프)는 비중 축소와 같은 행을 쓴다** (docs/infra.md 25.268).
    # 예전에는 3Y 창만, 신호 기준일과 무관하게 가장 새 행을 조인했다 — 상장 1~3년 종목은 3Y 가 비어
    # 리포트에 MDD 가 없는데 비중은 1Y MDD 로 줄었고, 신호 뒤에 계산된 지표가 리포트에 섞였다.
    # 신호 작업의 `load_metrics`(창 3Y→1Y, as_of 이하, 판)를 그대로 쓴다
    from batch.jobs import signals as signals_job

    지표 = signals_job.load_metrics(client, country, str(as_of))
    rows = [
        report_picks.SignalRow(
            stock_id=int(r["stock_id"]),
            ticker=str(r["ticker"]),
            name=str(r["name"]),
            market=str(r["market"]),
            horizon=str(r["horizon"]),
            signal_type=str(r["signal_type"]),
            currency=str(r["currency"]),
            buy_zone_low=r["buy_zone_low"],
            buy_zone_high=r["buy_zone_high"],
            suggested_weight_pct=r["suggested_weight_pct"],
            suggested_amount=r["suggested_amount"],
            size_reduction=float(r["size_reduction"] or 0),
            tranche_plan=report_picks.parse_json(r["tranche_plan"], []),
            rationale_text=str(r["rationale_text"] or ""),
            as_of_date=str(r["as_of_date"]),
            total_score=r["total_score"],
            rank_in_market=r["rank_in_market"],
            factor_scores=report_picks.parse_json(r["factor_scores"], {}),
            close=r["close"],
            close_date=r["close_date"],
            # 근거표도 리포트에 싣는다 — 웹 리포트 화면이 1부 추천마다 펼친다 (docs/infra.md 25.349)
            # 근거 자료가 배열·null 이면 dict 가 아니다 — `.get` 이 AttributeError 로 리포트 전체를
            # 죽였다 (25.818, 리포트 감사)
            criteria=(근거.get("criteria") if isinstance(근거 := report_picks.parse_json(r["rationale_data"], {}), dict)
                      else None) or [],
            cagr=지표.get(int(r["stock_id"]), {}).get("cagr"),
            mdd=지표.get(int(r["stock_id"]), {}).get("mdd"),
            sharpe=지표.get(int(r["stock_id"]), {}).get("sharpe"),
            perf_window=지표.get(int(r["stock_id"]), {}).get("window"),
            perf_as_of=지표.get(int(r["stock_id"]), {}).get("as_of_date"),
            sector=r["sector"],
            # **종합 점수에 실제로 들어간 센티먼트** (docs/infra.md 25.323). 1부가 "항상 분리 표시" 해야 하는데
            # 읽는 길이 없어 한 번도 찍히지 않았다
            sentiment=r["sentiment_score"],
        )
        for r in rs.dicts()
    ]
    return rows, str(as_of)


def collect_prices(
    client: TursoClient, market: str, trade_date: str
) -> tuple[list[dict[str, Any]], list[str], str]:
    """시장별 수집 경로로 넘긴다. 두 시장은 원천도 응답 모양도 다르다."""
    if market.upper() == "KR":
        return collect_kr_prices(client, trade_date)
    return collect_us_prices(client, trade_date)


def collect_us_prices(
    client: TursoClient, trade_date: str
) -> tuple[list[dict[str, Any]], list[str], str]:
    """미국 전종목 시세를 야후에서 받아 저장한다.

    Step 1 에서는 AAPL 한 종목만 받았다. 그 상태로는 20 거래일이 쌓이지 않아
    미국 유니버스의 거래대금 판정이 통째로 비었고, 그래서 성과 지표도 스코어도
    미국에는 낼 수 없었다. 마스터에 있는 종목 전부로 넓혀 그 구멍을 막는다.

    국내와 다른 점이 셋이다.

      1. **거래대금을 주지 않는다.** 종가 × 거래량으로 갈음한다.
         추정치인 이유와 그래도 쓰는 이유는 yfinance_src.estimate_turnover 에 적었다
      2. **한 번의 호출로 전종목이 오지 않는다.** 심볼을 조각으로 나눠 부르므로
         호출 수가 종목 수에 비례한다. 한국거래소 경로와 성격이 다르다
      3. **창 안의 모든 거래일을 저장한다.** 하루라도 실패하면 그날이 영영 비는데,
         20일 평균은 하루만 비어도 종목이 통째로 판정에서 빠진다
    """
    from batch.sources import yfinance_src

    targets = _us_symbol_ids(client, include_etf=True)  # 추천·보유 ETF 종가도 함께 (25.896)
    if not targets:
        raise RuntimeError(
            "미국 종목 마스터가 비어 있습니다. python -m batch.jobs.universe --market US 를 먼저 돌리세요"
        )

    symbols = sorted(targets)
    warnings: list[str] = []

    result = yfinance_src.fetch_daily_bars(symbols, lookback_days=US_LOOKBACK_DAYS)

    # 호출 수는 조각 수다. 사람이 어림해 적으면 조각 크기를 바꿀 때 어긋난다.
    db.record_api_call(
        client, "yfinance", count=yfinance_src.chunk_count(len(symbols))
    )

    if not result.ok:
        raise RuntimeError(f"미국 시세 수집 실패: {result.error or '알 수 없는 실패'}")
    if result.error:
        warnings.append(_drop_never_priced(client, result.error, symbols, result.data, targets))

    stored_total, tickers_seen, unsettled = _store_us_bars(
        client, result.data, trade_date, targets, result.source, notes=warnings
    )

    # **어긋난 줄 알면서 말하지 않고 있었다** (docs/infra.md 25.165). 감지는 `log.info` 에만
    # 남아 Actions 로그를 열지 않으면 보이지 않는다 — 그리고 우리는 그 로그를 못 읽는다(25.17)
    from datetime import date as _d

    from batch.jobs import refresh_us_adjusted as _adj

    try:
        밀림 = _adj.pending_note(client, _d.fromisoformat(trade_date))
    except Exception as exc:  # noqa: BLE001 — 말해 주는 일이 시세 수집을 죽이면 안 된다
        밀림 = None
        log.warning("수정주가 대기열을 읽지 못했습니다: %s", exc)
    if 밀림:
        warnings.append(밀림)

    # 미국 권장 금액을 달러로 내려면 환율이 필요하다(docs/signals.md 3.4). 실패해도 시세는 살린다.
    from batch.jobs import fx as fx_job

    try:
        _, fx_warnings = fx_job.collect(client)
    except Exception as exc:  # noqa: BLE001
        fx_warnings = [_실패문("환율 수집", exc)]
    warnings.extend(fx_warnings)
    warnings.extend(_collect_index_prices(client))

    if unsettled:
        # 배치는 장 시작 전에 돌지만 장중에 수동 실행하면 진행 중인 가격이 온다.
        # 그걸 종가로 저장하면 나중에 백테스트가 잘못된 값을 본다.
        warnings.append(
            f"확정 거래일({trade_date}) 이후 행 {unsettled:,}개는 장중 값이라 저장하지 않았습니다"
        )

    from datetime import date as _date

    trade_day = _date.fromisoformat(trade_date)
    cal.record_decision(client, "US", trade_day, is_open=True)
    suspicion = cal.suspect_calendar(is_open=True, rows_collected=stored_total)
    if suspicion:
        warnings.append(suspicion)

    if stored_total == 0:
        # **까닭을 함께 올린다** (docs/infra.md 25.615). 거래소 한도·야후 폴백 실패 같은 원인은 경고에만 있어,
        # 실패 알림에는 "한 건도 저장하지 못했습니다" 만 갔다
        raise RuntimeError(f"{trade_date} 미국 시세를 한 건도 저장하지 못했습니다" + _까닭(warnings))

    # 요약은 경고가 아니다. collect_kr_prices 주석 참고.
    summary = f"미국 {tickers_seen:,}종목 / {stored_total:,}행 저장 (대상 {len(symbols):,}종목)"

    # 리포트에는 지켜보는 종목만 싣는다. 추천 선정은 Step 9 에서 붙는다.
    rows = _report_rows_from_db(client, trade_date, WATCHED["US"])
    return rows, warnings, summary


def _us_symbol_ids(client: TursoClient, *, include_etf: bool = False) -> dict[str, int]:
    """야후 심볼 → stock_id. 심볼이 없는 행은 부를 수 없으므로 뺀다.

    **ETF 는 시세를 받을 때만 넣는다** (docs/infra.md 25.896). 추천·보유 ETF 를 stocks 에 이었는데(`asset_type =
    'etf'`),
    주식수(`us_shares`)·재무(`us_financials`)는 ETF 에 없어 SEC 를 헛되이 부른다.
    시세(`collect_us_prices`·`backfill_us`)만
    `include_etf=True` 로 부른다
    """
    rs = client.execute(
        "SELECT yahoo_symbol, id FROM stocks"
        " WHERE country = 'US' AND status = 'active' AND yahoo_symbol IS NOT NULL"
        + ("" if include_etf else " AND asset_type = 'stock'")
    )
    return {str(row[0]): int(row[1]) for row in rs.rows if row[0]}


def find_drift(client: TursoClient, bars: list, trade_date: str, ids: dict[str, int]) -> list[Any]:
    """종목마다 받은 창에서 **DB 에도 있는 가장 오래된** 날짜 하나로 저장된 조정 비율과 견준다 (docs/adjust.md 8장).

    기업행위가 최근에 있었으면 그 이전 날짜 전부가 한 번에 바뀌므로 한 날짜면 충분하다.
    **창의 맨 앞 날짜가 DB 에 없으면 그다음 날짜로 간다** (docs/infra.md 25.601, 감사).
    예전에는 맨 앞 하나만 보고 없으면 건너뛰어,
    쉬었다 돌아와 구멍을 메운 뒤(`turso-return` 의 짧은 백필) 분할 전 행과 분할 뒤 창이 이어지는 종목을 놓쳤다 —
    가격 이력에 가짜 절벽이 영구히 남았다. 창 전체 날짜를 종목 200개씩 한 번에 읽는다(창은 보통 10일 안팎).
    """
    from batch.services import adjust_drift as drift

    창: dict[int, dict[str, drift.Sample]] = {}
    for bar in bars:
        stock_id = ids.get(bar.ticker)
        if stock_id is None or bar.close is None or bar.adj_close is None or bar.date >= trade_date:
            continue
        창.setdefault(stock_id, {})[bar.date] = drift.Sample(bar.date, float(bar.close), float(bar.adj_close))
    if not 창:
        return []

    처음 = min(d for 날들 in 창.values() for d in 날들)
    저장: dict[int, dict[str, drift.Sample]] = {}
    stock_ids = list(창)
    for start in range(0, len(stock_ids), 200):
        chunk = stock_ids[start : start + 200]
        rs = client.execute(
            "SELECT stock_id, date, close, adj_close FROM prices"
            f" WHERE date >= ? AND date < ? AND close IS NOT NULL AND stock_id IN ({', '.join(['?'] * len(chunk))})",
            [처음, trade_date, *chunk],
        )
        # 수정종가가 없는 옛 행도 읽는다 — 분할은 종가로 본다 (docs/infra.md 25.498)
        for row in rs.rows:
            day = str(row[1])
            저장.setdefault(int(row[0]), {})[day] = drift.Sample(
                day, float(row[2]), None if row[3] is None else float(row[3])
            )

    stored: dict[int, drift.Sample] = {}
    oldest: dict[int, drift.Sample] = {}
    for stock_id, 날들 in 창.items():
        겹침 = sorted(set(날들) & set(저장.get(stock_id, {})))
        if 겹침:
            stored[stock_id] = 저장[stock_id][겹침[0]]
            oldest[stock_id] = 날들[겹침[0]]
    return drift.detect(stored, oldest)


def queue_drift(client: TursoClient, drifts: list[Any], now: str) -> int:
    """어긋난 종목을 대기열에 적는다. 이미 대기 중이면 첫 감지를 남긴다."""
    if not drifts:
        return 0
    # 분할 감지일 기록은 대기열과 **같은 묶음**으로 쓴다 (25.541, 교차검증) — 따로 쓰다 기록만 실패하면,
    # 다음 날은 저장된 표본이 이미 분할 뒤 값이라 다시 감지하지 못해 그 종목의 분할 기록이 영영 빠졌다
    분할 = [d.stock_id for d in drifts if d.ratio_before == d.ratio_after]
    기록문: list[tuple[str, list[Any]]] = []
    if 분할:
        from batch.services import adjust_drift as drift_svc

        기록 = db.get_setting(client, "us_split_detections", {})
        기록문 = [db.setting_statement(drift_svc.SPLIT_LOG_KEY, drift_svc.merge_split_log(기록, 분할, now))]
    # Turso 파이프라인은 문장마다 자동 커밋이라 한 묶음이어도 앞부분만 쓰일 수 있다. 실패하면 **그 종목의 오늘
    # 시세를 저장하지 않는다**(`_store_us_bars`, 25.548) — 표본이 분할 전 값으로 남아 다음 날 다시 감지된다.
    # **기록을 앞에 둔다** (25.553, 교차검증): 대기열만 쓰이고 끊기면 토요일 재수집이 이력을 고쳐 다음 날 감지가
    # 사라지고 기록이 영영 빠졌다. 기록만 쓰이고 끊기면 대기열이 빠져도 이력은 그대로라 다음 날 다시 감지된다
    client.batch(기록문 + [
        (
            "INSERT INTO adjust_refresh_queue (stock_id, detected_at, sample_date, ratio_before, ratio_after, done_at)"
            " VALUES (?, ?, ?, ?, ?, NULL)"
            " ON CONFLICT (stock_id) DO UPDATE SET"
            "   detected_at = CASE WHEN adjust_refresh_queue.done_at IS NULL THEN adjust_refresh_queue.detected_at"
            "                      ELSE excluded.detected_at END,"
            # **대기 중인 분할 표시(두 비율이 같음)는 지키지 않으면 사라진다** (docs/infra.md 25.506, 교차검증).
            # 분할을 기다리는 동안 배당락이 오면 비율이 달라져 분할 우선(`refresh_us_adjusted.pending`)을 잃었다.
            # 재수집이 끝난 뒤까지 지키지는 않는다 — 감지일은 `adjust_drift.SPLIT_LOG_KEY` 에 따로 둔다 (25.535)
            "   sample_date = excluded.sample_date,"
            "   ratio_before = CASE WHEN adjust_refresh_queue.done_at IS NULL"
            "     AND adjust_refresh_queue.ratio_before = adjust_refresh_queue.ratio_after"
            "     THEN excluded.ratio_after ELSE excluded.ratio_before END,"
            "   ratio_after = excluded.ratio_after, done_at = NULL",
            [d.stock_id, now, d.date, d.ratio_before, d.ratio_after],
        )
        for d in drifts
    ])
    return len(drifts)


def _store_us_bars(
    client: TursoClient,
    bars: list,
    trade_date: str,
    ids: dict[str, int],
    source: str,
    detect_drift: bool = True,
    notes: list[str] | None = None,
) -> tuple[int, int, int]:
    """일봉을 prices 에 넣는다. (저장 행 수, 종목 수, 건너뛴 장중 행 수).

    detect_drift 면 넣기 전에 수정주가 어긋남을 찾아 대기열에 적는다(docs/adjust.md 8장).
    백필처럼 전 구간을 다시 받는 경우는 끈다 — 그게 곧 재수집이다.
    """
    from batch.sources import yfinance_src

    now = db.now_iso()
    rows_data: list[tuple] = []
    seen: set[int] = set()
    unsettled = 0

    보류: set[int] = set()
    if detect_drift:
        drifts: list[Any] = []
        try:
            drifts = find_drift(client, bars, trade_date, ids)
            queued = queue_drift(client, drifts, now)
            if queued:
                log.info("수정주가가 어긋난 종목 %d개를 재수집 대기열에 적었습니다", queued)
        except Exception as exc:  # noqa: BLE001 — 감지가 실패해도 시세는 저장한다
            # 적지 못한 종목은 **오늘 시세를 저장하지 않는다** (docs/infra.md 25.548, 교차검증). 저장하면 표본이
            # 조정 뒤 값으로 덮여 다음 날 다시 감지하지 못하고, 창보다 앞선 이력이 영영 재조정되지 않았다
            보류 = {d.stock_id for d in drifts}
            log.warning("수정주가 어긋남 기록 실패 — %d종목은 오늘 시세를 미룹니다: %s", len(보류), exc)
            if notes is not None:
                # 로그만 남기면 아무도 못 본다(25.17) — 리포트에 싣는다 (25.553, 교차검증). 감지 자체가 실패한 경우도
                # 말한다(25.555) — 그때는 누구를 미룰지 몰라 모두 저장되고, 분할이 있었다면 감지가 영영 빠진다
                미룸 = f"수정주가 어긋남을 대기열에 적지 못해 {len(보류)}종목의 오늘 시세를 미뤘습니다: {exc}"
                놓침 = f"수정주가 어긋남(분할·배당) 감지 실패 — 오늘 분할이 있었다면 놓쳤을 수 있습니다: {exc}"
                notes.append(미룸 if 보류 else 놓침)

    for bar in bars:
        stock_id = ids.get(bar.ticker)
        if stock_id is None or bar.close is None or stock_id in 보류:
            continue

        # 확정 거래일보다 뒤인 날짜는 아직 종가가 아니다.
        if bar.date > trade_date:
            unsettled += 1
            continue

        rows_data.append(
            (
                stock_id,
                bar.date,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.adj_close,  # 야후 Adj Close. 배당·분할 조정. 국내에는 없다
                bar.volume,
                yfinance_src.estimate_turnover(bar.close, bar.volume),
                bar.currency or "USD",
                source,
                now,
                # 등락률은 한국거래소만 준다. 미국은 야후 Adj Close 가 이미 조정돼 있어 필요 없다
                None,
            )
        )
        seen.add(stock_id)

    stored = db.bulk_upsert_prices(client, rows_data)
    return stored, len(seen), unsettled


def _record_pause(client: TursoClient, name: str, market: str, trade_date: str, trigger: str) -> None:
    """D1 에서 미국을 쉰 것을 skipped 로 남긴다. 기록이 한도로 막혀도 조용히 끝낸다(알림 없음)."""
    try:
        run_id = db.start_batch_run(client, job_name=name, market=market, trade_date=trade_date, trigger=trigger)
        db.finish_batch_run(client, run_id, status="skipped", step_log={"decision": US_PAUSED_ON_D1})
    except Exception as exc:
        if db.quota_reason(exc) is None:
            raise
        print(f"{NOTHING_DONE}{US_PAUSED_ON_D1} (DB 쓰기 한도로 기록은 남기지 못했습니다)")
        return
    print(f"{NOTHING_DONE}{US_PAUSED_ON_D1}")


def _record_skip(client: Any, name: str, market: str, decision: Any, trigger: str | None) -> None:
    """건너뛴 실행도 끝맺는다 (25.753 에 떼어 냄). 시작만 기록하면 status 가 'running' 인 채로 남아
    화면과 무응답 감시가 "아직 도는 중" 으로 읽는다. 운영에서 실제로 그랬다
    (2026-09-17, docs/infra.md 21절). 건너뜀은 실패가 아니므로 skipped 다."""
    skipped_id = db.start_batch_run(
        client,
        job_name=name,
        market=market,
        trade_date=decision.trade_date,
        trigger=trigger,
    )
    로그: dict[str, Any] = {"decision": decision.reason}
    if "휴장일" in decision.reason:
        # 휴장 판정도 달력 기록에 남긴다 — 버전과 근거(라이브러리·수동 목록)까지 (docs/infra.md 25.670, 감사)
        휴장일 = date.fromisoformat(decision.session_date)
        로그["calendar"] = f"{cal.library_version()} · {cal.holiday_basis(market, 휴장일)}"
        with contextlib.suppress(Exception):  # 기록 실패가 건너뜀을 막지 않는다
            cal.record_decision(client, market, 휴장일, is_open=False, note=decision.reason)
    db.finish_batch_run(client, skipped_id, status="skipped", step_log=로그)


def run(market: str, *, force: bool = False, dry_run: bool = False) -> int:
    """배치 한 번. 종료 코드를 돌려준다."""
    market = market.upper()
    name = job_name(market)
    started_at = datetime.now(UTC)
    trigger = db.trigger_source()

    client = TursoClient()
    try:
        decision = cal.decide(market, now=started_at, force=force)
        log.info("판단: %s", decision.reason)

        # D1 임시 운영 중에는 미국을 돌리지 않는다. D1 에는 미국 마스터·시세가 없어 돌려 봐야
        # "미국 종목 마스터가 비어 있습니다" 로 실패하고 평일마다 실패 알림을 보낸다 (docs/infra.md 25.11·25.14).
        # **DB 에 쓰기 전에** 본다 — 하루 쓰기 한도가 찬 날 마이그레이션 확인이 먼저 막히면 main 이
        # "한도로 건너뜁니다" 알림을 보낸다. 쉬는 이유는 한도가 아니다
        if market == "US" and decision.should_run and backend.resolved_backend() == backend.D1:
            _record_pause(client, name, market, decision.trade_date, trigger)
            return 0

        if not decision.should_run:
            # **돌 필요가 없는 실행은 DB 오류로 실패 알림을 내지 않는다** (docs/infra.md 25.753, 리포트 감사).
            # 예전에는 이 판정보다
            # 마이그레이션 확인이 먼저라, 휴장일·이미 끝난 날의 예비 예약에서 Turso 일시 오류가 나면 "배치
            # 실패"(한도면 "건너뜁니다")
            # 알림이 갔다. 건너뜀 기록은 남기려 애쓰되, 못 남겨도 조용히 끝낸다 — 할 일이 없는 실행이다
            깊이 = db.open_run_depth()
            try:
                _record_skip(client, name, market, decision, trigger)
            except Exception as exc:  # noqa: BLE001
                log.warning("건너뜀 기록을 남기지 못했습니다(알림 없음): %s", exc)
                # 시작 행만 들어간 채 끊겼으면 닫는다 — 'running' 으로 6시간 남아 /status 수동 실행 단추를 막았다
                # (25.755, 교차검증)
                with contextlib.suppress(Exception):  # 건너뜀은 실패가 아니다 — skipped 로 닫는다 (25.757)
                    db.fail_runs_opened_after(깊이, f"건너뜀 기록 도중 실패: {exc}", "skipped")
            print(f"{NOTHING_DONE}{decision.reason}")
            return 0

        applied = db.apply_migrations(client)
        if applied:
            log.info("마이그레이션 적용: %s", ", ".join(applied))


        if not force and db.has_successful_run(client, name, decision.trade_date):
            # **할 일 없는 실행 표식을 붙인다** (docs/infra.md 25.913, 감사). 예비 예약(국내 09:35·미국 겹치는 시각)이
            # 평일마다
            # 여기서 끝나는데 표식이 없어 워크플로가 이슈 #1 에 코멘트를 달고(아침 실제 실행 기록을 밀어냄, 25.84)
            # 시세 사본까지 맞췄다
            print(f"{NOTHING_DONE}{decision.trade_date} 거래일은 이미 성공했습니다. 중복 실행을 건너뜁니다")
            return 0
        # **그날 리포트를 이미 보냈으면 예약 재실행은 다시 보내지 않는다** (25.600, 감사). 발송 뒤 기록을 못
        # 닫으면(한도·시간 초과)
        # 성공 기록이 없어 예비 예약(국내 08:27·미국 12:27/13:27 UTC)이 리포트 전체를 한 번 더 보냈다. 수동
        # 실행(force)은 사람이 원한 것이라 막지 않는다
        if not force and reports.sent_exists(client, market, decision.trade_date):
            print(
                f"{NOTHING_DONE}{decision.trade_date} 거래일 리포트는 이미 보냈습니다"
                f"(실행 기록은 못 닫혔을 수 있습니다). 중복 발송을 건너뜁니다"
            )
            return 0

        # 예약 시각을 넘겨야 지연이 남는다 (docs/infra.md 25.327). 수동 실행(force)은 예약이 아니라 넘기지 않는다
        예약 = None if force else cal.scheduled_for(market, date.fromisoformat(decision.session_date))
        run_id = db.start_batch_run(
            client,
            job_name=name,
            market=market,
            trade_date=decision.trade_date,
            trigger=trigger,
            scheduled_for=예약,
        )
        지연 = (
            int((datetime.now(UTC) - datetime.fromisoformat(예약)).total_seconds()) if 예약 else None
        )

        step_log: dict[str, Any] = {"decision": decision.reason}

        # **DB 용량을 하루 한 번 잰다** (docs/infra.md 25.123). D1 무료 플랜의 500MB 상한은
        # 셋 중 **리셋이 없는** 한도다 — 차면 지우기 전까지 안 풀린다. 관리 API 한 번이라
        # 읽기·쓰기 예산을 쓰지 않는다. 못 재도 배치는 그대로 돈다
        용량상태 = db.measure_db_size(client)
        if 용량상태:
            step_log["db_size_state"] = 용량상태

        # **추천 ETF 를 stocks 에 잇는다** (docs/infra.md 25.896) — 시세를 받기 전에. 새로 통과한 ETF 도 그날부터
        # 종가가 쌓인다.
        # 곁다리다: 실패해도 시세·리포트는 그대로 간다(이미 이은 ETF 는 그대로 받는다)
        etf_이음경고: list[str] = []
        try:
            from batch.jobs import etf_link

            step_log["etf_link"] = etf_link.link(client, market)
        except Exception as exc:  # noqa: BLE001 — 곁다리. 경고로 남기고 시세 수집으로 간다
            etf_이음경고.append(f"추천 ETF 잇기 실패: {exc}")
        try:
            try:
                rows, warnings, summary = collect_prices(client, market, decision.trade_date)
                warnings = etf_이음경고 + warnings
            except KrxNotYet as 이름:
                # 실패 알림을 보내지 않고 **건너뜀** 으로 닫는다 — 성공이 아니므로 다음 실행(08:27·예비)이 다시 돈다
                # (25.604)
                db.finish_batch_run(client, run_id, status="skipped", step_log=step_log, error_text=str(이름))
                print(f"{NOTHING_DONE}{이름}")
                return 0
            step_log["collected"] = len(rows)
            step_log["summary"] = summary
            step_log["warnings"] = warnings
            # **잰 수를 사람에게 말한다** (docs/infra.md 25.142). 25.123 이 게이지를 만들었지만
            # 상태는 `step_log` 에만 있었다 — 배치 기록을 펼쳐야 보인다. 이 한도만 자정에
            # 안 풀리므로 경고는 일찍, 리포트에 나와야 한다
            용량경고 = db.db_size_note(client)
            if 용량경고:
                warnings.append(용량경고)
            # 장 시작 전 창을 놓친 늦은 실행이면 맨 앞에 말한다 (2026-10-01 사용자 결정, 25.845)
            if decision.late_minutes is not None:
                언제 = (f"개장 {-decision.late_minutes}분 뒤" if decision.late_minutes <= 0
                       else f"개장 {decision.late_minutes}분 전")  # fmt: skip
                warnings.insert(0, f"⚠️ {언제}에 만든 늦은 리포트입니다 — 장 시작 전 예약이 늦거나 실패했습니다")
        except Exception as exc:
            # **한도는 `main()` 이 건너뜀으로 닫는다** (docs/infra.md 25.6·25.664, 감사). 이 except 가 한도까지 잡아
            # `failed` 로 적고 "배치 실패" 텔레그램·종료 1(GitHub 실패 메일)을 냈다 — 아래 추천 단계(1804)와 같게 올린다
            if db.quota_reason(exc):
                raise
            db.finish_batch_run(
                client, run_id, status="failed", step_log=step_log, error_text=str(exc)
            )
            _notify_failure(client, market, name, str(exc), dry_run=dry_run)
            print(f"실패: {exc}")
            return 1

        # 추천 다시 계산 → 두 부로 싣기 (design.md 3.9)
        # dry-run 이어도 점수·신호는 저장된다. 발송만 하지 않는다.
        from batch.jobs import signals as signals_job

        warnings.extend(refresh_recommendations(market, decision.trade_date))
        warnings.extend(refresh_portfolio(market))
        currency = "KRW" if market == "KR" else "USD"
        # **추천 읽기가 실패해도 시세·매도 플래그는 보낸다** (docs/infra.md 25.818, 리포트 감사).
        # 신호 조인 한 번의 시간 초과가 바깥 except 로 빠져 손절(적색) 매도 플래그까지 그날 아침
        # 리포트가 통째로 나가지 않았다
        추천_오류: str | None = None
        배분_안함: str | None = None
        try:
            signal_rows, signals_as_of = load_signal_rows(
                client, "KR" if market == "KR" else "US", upto=decision.trade_date
            )
            settings = signals_job.load_settings(client, currency, decision.trade_date)
            # **돈에 닿는 설정이 범위를 벗어났으면 리포트에 싣는다** (docs/infra.md 25.170).
            # 권장 금액·비중 상한·목표가가 여기서 나온다
            warnings.extend(settings["setting_warnings"])
            # 추세 필터 배수도 돈에 닿는다(약세장 비중). 범위 밖이면 리포트에 싣는다 (docs/infra.md 25.260)
            warnings.extend(trend.load_settings(client)["warnings"])
            regimes, regime_line = report_regimes(client, market, signals_as_of, decision.trade_date)
        except Exception as exc:  # noqa: BLE001 — 추천만 빼고 리포트는 보낸다. 사유는 글과 실행 기록에 남긴다
            # 한도는 리포트로 덮지 않는다 — 수집 단계·바깥 except 와 같이 "건너뜀 한 번 알림" 경로로 (25.822, 교차검증)
            if db.quota_reason(exc):
                raise
            추천_오류 = str(exc)
            log.exception("추천을 읽지 못했습니다 — 시세·매도 플래그만 보냅니다")
            step_log["recommend"] = {"error": 추천_오류}
        if 추천_오류 is None:
            step_log["recommend"] = {
                "signals": len(signal_rows), "as_of": signals_as_of, "fx": settings["fx"],
                "regimes": {k: r.state for k, r in regimes.items()},
            }
            if signals_as_of and signals_as_of != decision.trade_date:
                warnings.append(f"추천은 {signals_as_of} 기준입니다 (오늘 계산분 없음)")
            묵은_거래일 = signal_age_sessions(market, signals_as_of, decision.trade_date)
            if 묵은_거래일 is not None and 묵은_거래일 > STALE_SIGNAL_SESSIONS:
                배분_안함 = (f"신호가 {묵은_거래일}거래일 전({signals_as_of}) 것이라 2부에서 금액을 배분하지 않았습니다"
                          " — 신호 계산이 멈췄는지 확인하세요")  # fmt: skip
            if settings["fx_missing"]:
                warnings.append("쓸 수 있는 환율(7일 이내)이 없어 미국 권장 금액을 내지 않았습니다")
        else:
            warnings.append(
                f"추천을 읽지 못해 이 리포트에는 1부·2부가 없습니다 ({추천_오류[:200]}) — 시세와 매도 플래그만 보냅니다"
            )

        fx_rate = (settings["fx"] or {}).get("rate") if 추천_오류 is None and settings.get("fx") else None
        # 아래 셋은 리포트 글보다 **먼저** 읽는다 — 읽기 실패 경고가 글에 실려야 한다 (docs/infra.md 25.219·25.220)
        # 추천을 못 읽은 날은 2부가 없다 — 보유를 읽으면 "여력 계산에 넣지 못했습니다" 같은 2부 경고만 남는다 (25.822,
        # 교차검증)
        holdings = load_holdings(client, currency, fx_rate, warnings) if 추천_오류 is None else []
        sell_flag_rows = _sell_flags_for_report(client, "KR" if market == "KR" else "US", warnings)
        if 추천_오류 is not None:
            # 추천 없이 — 1부·2부 글은 빼고 매도 플래그만 싣는다(표시와 알림만, 자동 매도 없음).
            # 2부 배분이 없으니 보유도 넘기지 않는다
            signal_rows, signals_as_of, regime_line = [], None, None
            settings = {"total_investable": 0, "fx_note": None, "max_weight_per_sector": 0.0,
                        "max_weight_per_stock": 0.0, "min_order_amount": 0, "fx_missing": False, "fx": None}
        # 상관 기반 분산에 쓸 시세 (docs/signals.md 3.6). **후보와 보유를 한 번에** 읽는다.
        # 후보는 2부에 실릴 상위 다섯뿐이라 보유 수가 읽는 양을 정한다
        corr_series = load_price_series(
            client,
            # 2부와 **같은 후보** — 근거표로 거른 뒤 상위 다섯 (docs/infra.md 25.507, 감사)
            [r.stock_id for r in report_picks.select_top(report_picks.with_criteria(signal_rows))]
            + [h.stock_id for h in holdings if h.asset_type != "etf"],  # ETF 는 대조하지 않는다 (25.908)
            signals_as_of or decision.trade_date,
            warnings,
        )
        message = formatter.daily_report(
            market=market,
            trade_date=decision.trade_date,
            rows=rows,
            started_at=started_at,
            warnings=warnings,
            summary=summary,
            delay_seconds=지연,
            composed_at=datetime.now(UTC),
        )
        step_log["correlation"] = {"stocks": len(corr_series), "holdings": len(holdings)}
        # **이름을 붙여 넘긴다** (2026-09-21, docs/infra.md 25.67).
        # 예전에는 열 개를 자리로 넘겼는데 그중 둘(`max_sector_pct` 30%, `max_stock_pct` 10%)이
        # 같은 실수(float)다. 둘이 뒤바뀌어도 타입도 테스트도 아무 말 하지 않고, 결과는
        # **한 종목이 포트폴리오의 30% 를 가져가는** 추천이다. 돈이 걸린 자리는 자리로 안 넘긴다
        # 다른 나라 리포트가 방금 2부에서 배분한 금액 — 원화 풀은 하나다 (docs/infra.md 25.508, 감사)
        예약, 예약_말 = (
            _other_report_reserved(client, market, currency, fx_rate, holdings, decision.trade_date)
            if 추천_오류 is None else (0.0, None)
        )
        step_log["reserved_by_other_report"] = 예약
        # 흔들기에 쓸 설정 가중치 — 점수 작업과 같은 `load_weights`. 경고는 점수 작업이 이미 리포트에 올렸으므로
        # 되풀이하지 않는다. 못 읽으면 흔들기 줄만 빠지고 리포트는 그대로 (25.947)
        흔들_가중치, 흔들_센티먼트 = _shake_weights(client) if signal_rows and 추천_오류 is None else (None, 0.0)
        # 건너뛴 추천 영수증 (docs/reports.md 3.3, 25.950) — 지난 리포트가 실제로 실은 종목·날·종가. 못 읽으면 줄만
        # 빠진다
        추천이력 = (
            _pick_history(client, market, decision.trade_date, warnings) if signal_rows and 추천_오류 is None else None
        )
        # 리포트 자기 채점 (docs/reports.md 3.4, 25.951) — 지난 1부 종목의 5·20일 뒤 성적을 머리에. 못 읽으면 줄만
        # 빠진다
        자기채점 = _self_grade(client, market, decision.trade_date, warnings) if 추천_오류 is None else None
        # 보유 종목 점수 영수증 (docs/reports.md 3.6, 25.953) — 샀을 때·지금 점수. 못 읽으면 절만 빠진다
        보유점수 = _holding_scores(client, signals_as_of, warnings) if holdings and 추천_오류 is None else []
        composed = report_picks.compose(
            signal_rows,
            settings["total_investable"],
            signals_as_of,
            currency=currency,
            budget_note=" · ".join(n for n in (settings.get("fx_note"), 예약_말) if n) or None,
            reserved=예약,
            max_sector_pct=settings["max_weight_per_sector"],
            sell_flags=sell_flag_rows,
            regime_line=regime_line,
            holdings=holdings if 추천_오류 is None else [],
            max_stock_pct=settings["max_weight_per_stock"],
            price_series=corr_series,
            # **이미 종목 통화로 환산된 값이다** (`load_settings`: 미국은 USDKRW 로 나눈다).
            # 2부가 상한·상관으로 깎고 나면 이 문턱 아래로 내려갈 수 있다 (docs/infra.md 25.96)
            min_order_amount=settings["min_order_amount"],
            fx_missing=settings["fx_missing"],
            no_allocation_reason=배분_안함,
            # 종목 줄의 매매 입력 딥링크 (25.944). 주소가 비어 있으면 붙지 않는다
            app_url=config.APP_URL or None,
            # 가중치 흔들기 (docs/reports.md 3.2, 25.947) — 점수 계산과 같은 설정값을 읽는다. 읽기 실패는 흔들기만 뺀다
            weights=흔들_가중치,
            sentiment_weight=흔들_센티먼트,
            pick_history=추천이력,
            self_grade_line=자기채점,
            holding_scores=보유점수,
        )
        if 추천_오류 is not None:
            # "오늘 추천할 종목이 없습니다" 는 계산했을 때의 말이다 — 못 읽은 날에 쓰지 않는다 (25.818)
            composed = dc_replace(
                composed, text="\n".join(report_sections.render_sell_flags(sell_flag_rows)) if sell_flag_rows else ""
            )
        if composed.text:
            message += "\n\n" + composed.text
        # 보유·추천 종목의 기업행위 일정 (docs/reports.md 3.7, 25.989) — 가격이 튀기 **전에** 안다.
        # 못 읽으면 절만 빠진다. 보유이면서 추천이면 "보유" 로 적는다(뒤가 이긴다)
        if market == "KR" and 추천_오류 is None:
            상위 = report_picks.select_top(report_picks.with_criteria(signal_rows))
            일정 = _corp_event_lines(
                client, decision.trade_date,
                {r.ticker: (r.name, "추천") for r in 상위} | {h.ticker: (h.name, "보유") for h in holdings},
                warnings,
            )
            if 일정:
                message += "\n\n" + "\n".join(일정)
                step_log["corp_events"] = len(일정) - 1
            # 추천인데 외국인·기관이 팔고 증권사가 목표가를 내리는 종목 (docs/reports.md 3.9, 25.1001) — 참고만
            엇갈림 = _divergence_lines(client, decision.trade_date, 상위, warnings)
            if 엇갈림:
                message += "\n\n" + "\n".join(엇갈림)
                step_log["divergence"] = len(엇갈림) - 1

        if dry_run:
            print("--- 발송하지 않음 (dry-run) ---")
            print(message)
            # 성공으로 남기면 그날 진짜 배치가 중복으로 판정돼 건너뛴다.
            # 확인용 실행이 실제 리포트를 막으면 안 된다.
            db.finish_batch_run(
                client, run_id, status="dryrun", step_log={**step_log, "sent": False}
            )
            return 0

        # **고지를 먼저 붙인다** (docs/infra.md 25.325). 예전에는 붙이기 전의 글을 저장하고 `send` 가 붙여서
        # 저장한 본문(`summary_text`)과 보낸 텔레그램이 늘 한 줄 달랐다 — "본문은 보낸 글 그대로" (docs/reports.md)
        message = telegram.append_disclaimer(message)

        # 보내기 전에 남긴다. 발송이 실패해도 "만들었지만 못 보냈다" 가 화면에 보여야 한다 (docs/reports.md)
        report_id: int | None = None

        def _저장() -> int | None:
            try:
                rid = reports.store_report(
                    client,
                    market=market,
                    trade_date=decision.trade_date,
                    status="partial" if warnings else "success",
                    message=message,
                    warnings=warnings,
                    items=reports.items_from(composed, sell_flag_rows, warnings, regime_line),
                    batch_run_id=run_id,
                )
                step_log["report_id"] = rid
                return rid
            except Exception as exc:  # noqa: BLE001 — 저장이 막혀도 리포트는 보낸다
                log.warning("리포트를 저장하지 못했습니다: %s", exc)
                warnings.append(_실패문("리포트 저장", exc))
                return None

        # **이미 보낸 그날 리포트가 있으면 새 것을 보낸 뒤에 덮는다** (docs/infra.md 25.567, 감사). `--force` 재실행이
        # 발송 전에 저장하며 아침 행을 지웠고, 재발송이 실패하면 사용자가 받은 본문이 이력에서 사라지고 "못 보냄" 으로
        # 보였다 — 발송 시각도 잃어 다음 리포트에 이미 보여 준 매도 플래그 NEW 가 다시 붙었다. 처음 보내는 날은 예전처럼
        # 먼저 저장한다(발송이 실패해도 "만들었지만 못 보냈다" 가 보이게, docs/reports.md)
        보낸_것_있음 = reports.sent_exists(client, market, decision.trade_date)
        if not 보낸_것_있음:
            report_id = _저장()

        def _웹에_없음() -> str:
            """이번 본문이 웹에 저장되지 않았을 때 붙일 말 (25.672·25.674, 교차검증).

            재실행이면 웹에 **먼저 보낸 리포트**가 남아 있고(`store_report` 는 실패하면 옛 행을 둔다),
            그날 첫 리포트면 웹에 아무것도 없다 — 둘을 가른다.
            """
            if report_id is not None:
                return ""
            if 보낸_것_있음:
                return f" — 단, {formatter.NOT_SAVED_MARK}(웹은 먼저 보낸 리포트)"
            return f" — {formatter.WEB_SAVE_FAILED_MARK}"

        try:
            message_ids = telegram.send(message)
            step_log["sent"] = True
            step_log["message_ids"] = message_ids
            if 보낸_것_있음:
                report_id = _저장()
                if report_id is None:
                    # **재실행 본문은 갔는데 웹에는 옛 리포트가 남았다** — 폰과 웹이 다르다는 것을 말한다
                    # (docs/infra.md 25.756,
                    # 리포트 감사). 예전에는 실행 기록의 경고로만 남아 사용자는 몰랐다
                    _notify_failure(client, market, name, f"방금 보낸 리포트를 저장하지 못했습니다{_웹에_없음()}",
                                    dry_run=dry_run, title="리포트 저장 실패")
            if report_id is not None:
                try:
                    reports.mark_sent(client, report_id, message_ids)
                except Exception as exc:  # noqa: BLE001
                    log.warning("발송 시각을 적지 못했습니다: %s", exc)
        except telegram.TelegramPartialError as exc:
            # **앞 조각은 이미 갔다** (docs/infra.md 25.414). 실패로 닫으면 다음 예약이 전체를 다시 보내 앞 조각이
            # 두 번 간다. 보낸 것으로 적고, 뒷부분이 빠졌다고 경고·알림으로 말한다 — 전체는 웹 리포트에 있다
            step_log["sent"] = "partial"
            step_log["message_ids"] = exc.sent_ids
            if 보낸_것_있음:
                report_id = _저장()  # 새 본문 앞부분이 갔다 — 새 것으로 덮는다
            if report_id is not None:
                try:
                    reports.mark_sent(client, report_id, exc.sent_ids)
                except Exception as mark_exc:  # noqa: BLE001
                    log.warning("발송 시각을 적지 못했습니다: %s", mark_exc)
            # 저장이 막혔으면(`report_id` 없음) "웹에서 보세요" 가 거짓이다 (25.672·25.674, 교차검증)
            어디서 = _웹에_없음() or " — 나머지는 웹의 오늘 리포트에서 보세요"
            warnings.append(f"텔레그램 리포트 {exc.total}조각 가운데 {len(exc.sent_ids)}조각만 보냈습니다{어디서}")
            # 리포트는 보내기 전에 저장했다 — 웹에도 잘린 줄이 보이게 다시 적는다 (docs/infra.md 25.419)
            if report_id is not None:
                try:
                    reports.mark_partial_send(client, report_id, warnings)
                except Exception as mark_exc:  # noqa: BLE001
                    log.warning("일부 발송 경고를 리포트에 적지 못했습니다: %s", mark_exc)
            _notify_failure(client, market, name, f"리포트 뒷부분 발송 실패: {exc}{_웹에_없음()}", dry_run=dry_run,
                            title="리포트 일부만 보냄")
        except telegram.TelegramUncertainError as exc:
            # **보냈는지 모른다** (docs/infra.md 25.493, 텔레그램 감사). 실패로 닫으면 뒤따르는 예비 트리거가
            # 리포트 전체를 다시 보내 두 번 받을 수 있다. 다시 보내지 않는 쪽으로 닫는다 — partial 은 성공으로 세므로
            # 재실행이 건너뛴다. 안 왔으면 웹의 오늘 리포트에 전체가 있다(보내기 전에 저장했다)
            step_log["sent"] = "unknown"
            # 이미 보낸 날의 재실행이면 **새 리포트로 덮는다** (25.568, 교차검증) — 새 본문의 뒤 조각은 확실히 갔을 수
            # 있는데, 웹은 옛 아침 본문 그대로라 "웹의 오늘 리포트를 보라" 가 옛 것을 가리켰다
            확실히_간 = list(getattr(exc, "sent_ids", None) or [])
            # 덮는 것은 **새 본문의 조각이 확실히 갔을 때만**이다 (25.570, 교차검증). 한 조각짜리가 "모름" 이면
            # 새 본문이 한 줄도 확실히 가지 않았는데 옛 행을 지워, 확실히 도착한 아침 리포트의 발송 시각이 사라지고
            # 다음 날 NEW 가 다시 붙었다. 확실히 간 조각이 있으면 그것으로 발송 시각을 적는다
            if 보낸_것_있음 and 확실히_간:
                report_id = _저장()
            if 보낸_것_있음 and not 확실히_간:
                # 덮지 않았다 — 웹에는 아침 리포트가 남아 있다.
                # "빠진 부분은 웹에 있다" 가 틀린 말이 되지 않게 적는다 (25.572)
                warnings.append(
                    "이번 재실행 본문은 확실히 간 조각이 없어 웹에 저장하지 않았습니다 — 웹은 먼저 보낸 리포트입니다"
                )
            if report_id is not None and 확실히_간:
                try:
                    reports.mark_sent(client, report_id, 확실히_간)
                except Exception as mark_exc:  # noqa: BLE001
                    log.warning("발송 시각을 적지 못했습니다: %s", mark_exc)
            # **뒤 조각이 실패했거나 모르면 "일부만 보냈다" 표지를 붙인다** (docs/infra.md 25.600, 감사). 확실히 간
            # 조각이 있으면 발송 시각을
            # 적는데 이 경로엔 `PARTIAL_SEND_MARK` 가 없어, 매도 플래그가 든 끝 조각이 확실히 안 갔는데도 다음
            # 리포트에서 NEW 가 빠졌다(25.512 의 모양)
            뒤_안감 = ("번째는 실패했습니다", "번째도 보냈는지 모릅니다", "그 뒤는 보내지 않았습니다")
            if any(말 in str(exc) for 말 in 뒤_안감):
                warnings.append(f"끝 조각까지 확실히 가지 않았습니다 — 리포트 일부 {PARTIAL_SEND_MARK}")
            warnings.append(
                # 시간 초과만이 아니라 보낸 뒤 끊긴 연결도 이 길이다 (25.613) — 까닭을 넓게 적는다 (25.618)
                "텔레그램 응답을 받지 못해(시간 초과·연결 끊김) 발송 여부를 알 수 없습니다"
                + (_웹에_없음() or " — 안 왔으면 웹의 오늘 리포트를 보세요")
                # 첫 조각만 모르고 나머지는 보냈으면 그 말을 붙인다 (25.567)
                + (f" ({exc})" if "머리" in str(exc) else "")
            )
            if report_id is not None:
                try:
                    reports.mark_partial_send(client, report_id, warnings)
                except Exception as mark_exc:  # noqa: BLE001
                    log.warning("발송 모름 경고를 리포트에 적지 못했습니다: %s", mark_exc)
            # 덮지 않았으면 알림에도 그 사실을 붙인다 (25.574, 교차검증) — `_모름으로` 의 "빠진 부분은 웹에 있다" 는
            # 새 본문 기준인데, 그때 웹은 먼저 보낸 리포트다. 경고만으로는 실행 기록에만 남아 사용자에게 닿지 않았다
            # 재실행이 확실히 간 조각이 있어 덮으려다 저장이 막힌 경우도 여기서 잡는다 (25.674, 교차검증)
            덧말 = _웹에_없음() or (
                f" — 단, {formatter.NOT_SAVED_MARK}(웹은 먼저 보낸 리포트)" if 보낸_것_있음 and not 확실히_간 else ""
            )
            _notify_failure(client, market, name, f"리포트 발송 여부 모름(다시 보내지 않음): {exc}{덧말}",
                            dry_run=dry_run, title="리포트 발송 여부 모름")
        except Exception as exc:
            return _send_failed(client, run_id, step_log, market, name, exc, dry_run)

        status = "partial" if warnings else "success"
        try:
            db.finish_batch_run(client, run_id, status=status, step_log=step_log)
        except Exception as exc:
            # **리포트는 이미 갔다** (docs/infra.md 25.567, 감사). 여기서 한도 예외를 올리면 `main()` 이 "오늘 리포트는
            # 나가지 않습니다" 를 보내 방금 받은 리포트와 반대 말을 했다. 한도면 쓸 수 없으니 기록은 못 닫는다 —
            # 로그로 남기고 성공으로 끝낸다(기록은 `running` 으로 남아 6시간 뒤 정리된다)
            # **한도가 아닌 오류도 같다** (docs/infra.md 25.600, 감사). 예전에는 한도만 삼키고 나머지(Turso 시간 초과
            # 등)는 올려,
            # 방금 리포트를 받았는데 "배치 실패 · 마지막 성공 어제" 가 왔고 기록이 failed 로 남아 예비 예약이 리포트를
            # 한 번 더 보냈다.
            # 다시 보내지 않는 것은 아래 "이미 보낸 리포트" 검사가 막는다(기록을 못 닫아도 보낸 행은 있다)
            사유 = "한도" if db.quota_reason(exc) else "오류"
            # **한도가 아니면 새 연결로 한 번 더 닫는다** (25.602, 교차검증). 기록 닫기와 발송 시각(`mark_sent`)이 같은
            # Turso 장애로 함께
            # 실패하면 `sent_at` 도 비어 위 "이미 보낸 리포트" 검사가 뚫렸고 예비 예약이 다시 보냈다. 기록이 `running`
            # 으로 남으면
            # 무응답 거짓 경보·수동 실행 잠금도 하루 갔다. 일시 오류는 새 연결이면 대개 풀린다
            if 사유 == "오류" and _새연결로_닫기(run_id, status, step_log):
                print(f"완료 ({status}, 기록은 새 연결로 닫음). 종목 {len(rows)}개, 거래일 {decision.trade_date}")
                return 0
            log.warning("리포트는 보냈지만 실행 기록을 닫지 못했습니다(%s): %s", 사유, exc)
            print(f"완료 ({status}, 기록 못 닫음 — {사유}). 종목 {len(rows)}개, 거래일 {decision.trade_date}")
            return 0
        print(f"완료 ({status}). 종목 {len(rows)}개, 거래일 {decision.trade_date}")
        return 0

    except Exception as exc:
        # **시세 수집 뒤에 깨져도 실패로 닫고 알린다** (docs/infra.md 25.231).
        # 전에는 수집 단계만 감싸서, 추천·리포트 단계의 예외는 `main()` 의 except 로 곧장 갔다 —
        # 거기서는 로그만 찍고 1 을 돌려준다. `guard()` 의 `fail_open_runs` 에 닿지 못해 기록은
        # `running` 으로 남았고(여섯 시간 뒤 "건너뜀" 으로 닫힌다), 텔레그램 실패 알림도 없었다.
        # CLAUDE.md: "배치 실패 시 텔레그램 실패 알림". 한도 오류는 `main()` 이 따로 다루므로 그대로 올린다
        if db.quota_reason(exc):
            raise
        db.fail_open_runs(str(exc))
        _notify_failure(client, market, name, str(exc), dry_run=dry_run)
        raise

    finally:
        client.close()


def _새연결로_닫기(run_id: int, status: str, step_log: dict) -> bool:
    """발송 뒤 기록을 새 연결로 한 번 더 닫는다 (docs/infra.md 25.602). 되면 True. 실패는 삼킨다(리포트는 이미 갔다)."""
    from batch.core.client import TursoClient as _Client

    try:
        새것 = _Client()
    except Exception:  # noqa: BLE001 — 새 연결도 못 열면 처음 경고로 남는다
        return False
    try:
        db.finish_batch_run(새것, run_id, status=status, step_log=step_log)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("새 연결로도 실행 기록을 닫지 못했습니다: %s", exc)
        return False
    finally:
        새것.close()


def _send_failed(
    client: TursoClient, run_id: int, step_log: dict, market: str, name: str, exc: Exception, dry_run: bool
) -> int:
    """리포트 발송이 실패했다 — **실패로 닫고 알린다** (docs/infra.md 25.370).

    예전에는 `partial` 로 닫고 알리지 않았다. 그런데 `partial` 은 "성공(경고 있음)" 으로 세는 값이라
    `has_successful_run` 이 다음 예약·재시도를 "이미 성공했습니다" 로 건너뛰었고, 무응답 감시도 그날을 돈 것으로 봤다.
    **리포트가 안 나간 날인데 어느 경로로도 알림이 없었다.** 텔레그램이 막혀 실패 알림도 못 갈 수 있지만,
    일시 오류면 닿는다. 그리고 `failed` 면 다음 예약이 다시 돌고 D1 따라잡기가 "아침 리포트가 안 나갔다" 로 본다.
    저장된 리포트는 남는다(`sent_at` 이 비어 "만들었지만 못 보냈다" 가 보인다).
    """
    error_text = f"발송 실패: {exc}"
    db.finish_batch_run(client, run_id, status="failed", step_log={**step_log, "sent": False}, error_text=error_text)
    _notify_failure(client, market, name, error_text, dry_run=dry_run)
    print(f"수집은 됐으나 발송에 실패했습니다: {exc}")
    return 1


#: 리포트 거래일보다 이만큼 넘게 앞선 신호로는 2부에서 금액을 배분하지 않는다 (docs/infra.md 25.820). 하루(어제 신호를
#: 그대로 쓰는 날,
#: 25.547)는 배분한다 — 신호가 한 번 건너뛴 것은 흔하고 매수 구간이 하루에 크게 바뀌지 않는다
STALE_SIGNAL_SESSIONS = 1


#: 기업행위 일정을 리포트에 싣는 앞날 (25.989). 배당락·권리락은 기준일 하루 전 거래일이다 — 그 주에 알면 된다
CORP_EVENT_DAYS = 10
_행사_이름 = {"dividend": "배당 기준일", "bonus": "무상증자 기준일", "rights": "유상증자 기준일",
           "split": "액면교체·합병·분할 기준일"}  # fmt: skip


def _corp_event_lines(
    client: TursoClient, trade_date: str, 종목: dict[str, tuple[str, str]], warnings: list[str] | None = None
) -> list[str]:
    """보유·추천 종목의 기업행위 일정 (docs/reports.md 3.7, 25.989). `kr_corp_events`(KIS 예탁원, 25.988).

    표가 없거나(마이그레이션 전·KIS 키 없음) 못 읽으면 빈 목록 — 곁다리다. 배당은 거의 모든 종목에 있어 시끄러우니
    **보유 종목만** 싣고, 무상·유상·액면교체는 추천에도 싣는다(가격이 크게 바뀐다)."""
    if not 종목:
        return []
    끝 = (date.fromisoformat(trade_date) + timedelta(days=CORP_EVENT_DAYS)).isoformat()
    try:
        rows = client.execute(
            "SELECT e.code, e.kind, e.record_date FROM kr_corp_events e JOIN json_each(?) j ON j.value = e.code"
            " WHERE e.record_date > ? AND e.record_date <= ? ORDER BY e.record_date, e.code",
            [json.dumps(sorted(종목)), trade_date, 끝],
        ).dicts()
    except Exception as exc:  # noqa: BLE001 — 곁다리. 표가 없으면(0047 전) 조용히, 그 밖의 실패는 경고로
        if not db.표가_없나(exc) and warnings is not None:
            warnings.append(_실패문("기업행위 일정 읽기", exc))
        return []
    lines = []
    for r in rows:
        이름, 왜 = 종목[r["code"]]
        if r["kind"] == "dividend" and 왜 != "보유":
            continue
        lines.append(f"· {이름}({r['code']}) {_행사_이름.get(r['kind'], r['kind'])} {r['record_date'][5:]} ({왜})")
    return [f"📅 기업행위 일정 ({CORP_EVENT_DAYS}일 안, 예탁원·KIS)", *lines] if lines else []


DIVERGENCE_FLOWS_SQL = (
    "SELECT f.stock_id, f.date, f.frgn_net_amt, f.orgn_net_amt FROM kr_flows f"
    " JOIN json_each(?) j ON j.value = f.stock_id"
    " WHERE f.date <= ? AND f.date > ? AND f.frgn_net_amt IS NOT NULL AND f.orgn_net_amt IS NOT NULL"
    " ORDER BY f.stock_id, f.date DESC"
)
DIVERGENCE_OPINIONS_SQL = (
    "SELECT o.stock_id, o.date, o.broker, o.target_price FROM kr_opinions o JOIN json_each(?) j ON j.value = o.stock_id"
    " WHERE o.date <= ? AND o.date >= ?"
)


def _divergence_lines(
    client: TursoClient, trade_date: str, picks: list, warnings: list[str] | None = None
) -> list[str]:
    """국내 추천 종목의 점수 × 수급 × 증권사 목표가 엇갈림 (docs/reports.md 3.9, 25.1001).

    표가 없거나(0046·0047 전) 못 읽으면 빈 목록 — 곁다리다. 같은 종목의 여러 기간 신호는 한 번만 센다."""
    종목: dict[int, tuple[str, str]] = {}
    for r in picks:
        종목.setdefault(int(r.stock_id), (r.name, r.ticker))
    if not 종목:
        return []
    오늘 = date.fromisoformat(trade_date)
    ids = json.dumps(sorted(종목))
    try:
        흐름 = client.execute(
            DIVERGENCE_FLOWS_SQL, [ids, trade_date, (오늘 - timedelta(days=divergence.FLOW_DAYS * 3)).isoformat()]
        ).rows
        의견 = client.execute(DIVERGENCE_OPINIONS_SQL, [ids, trade_date, (오늘 - timedelta(days=400)).isoformat()]).rows
    except Exception as exc:  # noqa: BLE001 — 곁다리. 표가 없으면 조용히, 그 밖의 실패는 경고로
        if not db.표가_없나(exc) and warnings is not None:
            warnings.append(_실패문("수급·의견 엇갈림 읽기", exc))
        return []
    합: dict[int, list[int]] = {}
    for sid, _d, frgn, orgn in 흐름:
        s = 합.setdefault(int(sid), [0, 0, 0])
        if s[0] < divergence.FLOW_DAYS:
            s[0] += 1
            s[1] += int(frgn)
            s[2] += int(orgn)
    목표: dict[int, list[tuple[str, str, float | None]]] = {}
    for sid, d, broker, target in 의견:
        목표.setdefault(int(sid), []).append((str(d), str(broker), float(target) if target else None))
    since = (오늘 - timedelta(days=divergence.OPINION_DAYS)).isoformat()
    views = []
    for sid, (이름, 티커) in 종목.items():
        n, frgn, orgn = 합.get(sid, [0, 0, 0])
        up, down = divergence.target_changes(목표.get(sid, []), since)
        views.append(divergence.View(이름, 티커, n, frgn, orgn, up, down))
    return divergence.render(views)


def _holding_scores(
    client: TursoClient, as_of: str | None, warnings: list[str]
) -> list[holding_scores.HoldingScore]:
    """보유(ETF 제외)마다 지금 점수(추천 화면과 같은 규칙: 기준일 ≤ 신호 기준일, 그날의 가장 새 판)와 **지금 보유분**의
    점수가 있는 가장 이른 매수의 `score_at_trade` (docs/reports.md 3.6). 못 읽으면 빈 목록과 경고.

    매수는 `trade_date >= positions.first_buy_date` 만 본다 — 다 팔고 다시 산 종목에 닫힌 옛 매수의 점수가 붙지 않게
    (매도 플래그 25.286 과 같은 규칙, 25.962). 두 점수의 잣대(판·팩터 가중치·센티먼트 가중치)가 다르면 차이를 적지
    않는다(25.209 와 같은 판정)."""
    from batch.jobs.sell_flags import _가중치_다름, _팩터가중치_다름

    try:
        rs = client.execute(
            "SELECT p.stock_id, COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.market,"
            "  sc.total_score AS score_now, sc.rank_in_market, sc.as_of_date,"
            "  sc.calc_version, sc.weights_json, sc.sentiment_weight_used,"
            "  (SELECT t.score_at_trade"
            " FROM trades t WHERE t.stock_id = p.stock_id AND t.side = 'buy' AND t.score_at_trade IS NOT NULL"
            "     AND t.trade_date >= p.first_buy_date ORDER BY t.trade_date, t.id LIMIT 1) AS score_at_trade,"
            "  (SELECT t.trade_date"
            " FROM trades t WHERE t.stock_id = p.stock_id AND t.side = 'buy' AND t.score_at_trade IS NOT NULL"
            "     AND t.trade_date >= p.first_buy_date ORDER BY t.trade_date, t.id LIMIT 1) AS first_buy,"
            "  (SELECT t.snapshot_as_of"
            " FROM trades t WHERE t.stock_id = p.stock_id AND t.side = 'buy' AND t.score_at_trade IS NOT NULL"
            "     AND t.trade_date >= p.first_buy_date ORDER BY t.trade_date, t.id LIMIT 1) AS score_at_trade_date"
            " FROM positions p JOIN stocks s ON s.id = p.stock_id"
            " LEFT JOIN scores sc ON sc.stock_id = p.stock_id"
            "  AND sc.as_of_date = (SELECT MAX(as_of_date) FROM scores WHERE stock_id = p.stock_id AND as_of_date <= ?)"
            "  AND sc.calc_version = (SELECT MAX(c.calc_version) FROM scores c"
            "                         WHERE c.stock_id = sc.stock_id AND c.as_of_date = sc.as_of_date)"
            " WHERE p.quantity > 0 AND s.asset_type != 'etf' ORDER BY s.market, name",
            [as_of or "9999-12-31"],
        )
        rows = rs.dicts()
        for r in rows:
            # 매수 때 점수의 잣대 — 매도 플래그(jobs/sell_flags)와 같은 행·같은 판정. 둘 다 알 때만 다르다고 한다
            if r.get("score_at_trade") is None or r.get("calc_version") is None or not r.get("score_at_trade_date"):
                continue
            그때 = client.execute(
                "SELECT weights_json, calc_version, sentiment_weight_used FROM scores"
                " WHERE stock_id = ? AND as_of_date = ? ORDER BY calc_version DESC LIMIT 1",
                [r["stock_id"], r["score_at_trade_date"]],
            ).dicts()
            if 그때:
                g = 그때[0]
                r["same_yardstick"] = not (
                    int(g["calc_version"]) != int(r["calc_version"])
                    or _팩터가중치_다름(g["weights_json"], r["weights_json"])
                    or _가중치_다름(g.get("sentiment_weight_used"), r.get("sentiment_weight_used"))
                )
    except Exception as exc:  # noqa: BLE001 — 영수증은 곁다리다. 경고로 말하고 리포트는 그대로
        warnings.append(f"보유 종목 점수를 읽지 못해 2부의 점수 줄을 뺐습니다: {exc}")
        return []
    return holding_scores.from_rows(rows)


def _self_grade(client: TursoClient, market: str, trade_date: str, warnings: list[str]) -> str | None:
    """지난 리포트 1부 종목 ⋈ 신호 성적 (docs/reports.md 3.4). 못 읽으면 None 과 경고 — 채점은 곁다리라 리포트를 막지
    않는다."""
    try:
        rs = client.execute(
            "SELECT i.stock_id, r.trade_date, MAX(o.ret_5d) AS ret_5d, MAX(o.ret_20d) AS ret_20d"
            " FROM report_items i JOIN daily_reports r ON r.id = i.report_id"
            " JOIN signal_outcomes o ON o.stock_id = i.stock_id"
            "  AND o.as_of_date = json_extract(i.payload_json, '$.as_of_date')"
            " WHERE r.market = ? AND r.trade_date < ? AND i.section = 'recommend' AND i.stock_id IS NOT NULL"
            " GROUP BY i.stock_id, r.trade_date",
            [market, trade_date],
        )
    except Exception as exc:  # noqa: BLE001 — 자기 채점은 곁다리다. 경고로 말하고 리포트는 그대로
        warnings.append(f"지난 추천의 성적을 읽지 못해 자기 채점 줄을 뺐습니다: {exc}")
        return None
    return self_grade.line(self_grade.grade(rs.dicts()))


def _pick_history(
    client: TursoClient, market: str, trade_date: str, warnings: list[str]
) -> dict[int, pick_history.PickHistory] | None:
    """지난 리포트(오늘 거래일 전)의 1부 항목 — 종목·날·그때 적힌 종가 (docs/reports.md 3.3). 못 읽으면 None 과 경고."""
    try:
        rs = client.execute(
            "SELECT i.stock_id, r.trade_date, json_extract(i.payload_json, '$.close') AS close"
            " FROM report_items i JOIN daily_reports r ON r.id = i.report_id"
            " WHERE r.market = ? AND r.trade_date < ? AND i.section = 'recommend' AND i.stock_id IS NOT NULL",
            [market, trade_date],
        )
    except Exception as exc:  # noqa: BLE001 — 영수증은 곁다리다. 리포트를 막지 않고 경고로 말한다
        warnings.append(f"지난 리포트 이력을 읽지 못해 추천 횟수 줄을 뺐습니다: {exc}")
        return None
    return pick_history.summarize(rs.dicts())


def _shake_weights(client: TursoClient) -> tuple[dict[str, float] | None, float]:
    """가중치 흔들기(docs/reports.md 3.2)에 쓸 설정 가중치. 못 읽으면 (None, 0) — 흔들기 줄만 빠진다."""
    from batch.jobs import scores

    try:
        weights, sentiment_weight, _경고 = scores.load_weights(client)
    except Exception as e:  # noqa: BLE001 — 표시용 부가 정보라 리포트를 막지 않는다
        log.warning("가중치 흔들기 — 설정 가중치를 읽지 못해 뺀다: %s", e)
        return None, 0.0
    return weights, sentiment_weight


def signal_age_sessions(market: str, signals_as_of: str | None, trade_date: str) -> int | None:
    """신호 기준일 뒤로 리포트 거래일까지 몇 거래일이 지났나. 같은 날이면 0. 모르면 None."""
    if not signals_as_of:
        return None
    try:
        d, end = date.fromisoformat(signals_as_of[:10]), date.fromisoformat(trade_date[:10])
        n = 0
        while d < end and n < 60:
            d = cal.next_session(market, d)
            if d <= end:
                n += 1
        return n
    except Exception:  # noqa: BLE001 — 달력을 못 쓰면 나이를 모른다. 배분을 막지 않는다(경고는 위에 따로 있다)
        return None


def _notify_failure(
    client: TursoClient, market: str, name: str, error_text: str, dry_run: bool, title: str = "배치 실패"
) -> None:
    """실패를 알린다. 알림 자체가 실패해도 배치 결과를 덮지 않는다.

    **DB 를 못 읽어도 알림은 나가야 한다.** 2026-09-17 에 Turso 월 한도를 넘겨 읽기가
    막혔을 때, 마지막 성공을 조회하다가 예외가 나면서 알림이 통째로 사라졌다
    (docs/infra.md 23절). 배치가 죽은 데다 그 사실조차 전해지지 않는 것이 가장 나쁘다.
    조회는 따로 감싸고, 실패하면 "마지막 성공을 알 수 없다" 로 보낸다. 텔레그램은
    DB 를 타지 않는다.
    """
    last = None
    못읽음 = False
    try:
        last = db.last_successful_run(client, name)
    except Exception:  # noqa: BLE001 — 실패 알림을 보내는 길이다. 읽기 실패가 알림을 막으면 안 된다
        log.warning("마지막 성공 기록을 읽지 못했습니다 (DB 가 막혔을 수 있다)")
        못읽음 = True

    try:
        message = formatter.failure_alert(
            market=market, job_name=name, error_text=error_text, last_success=last, title=title, last_unread=못읽음
        )
        if dry_run:
            print("--- 실패 알림 (dry-run) ---")
            print(message)
        else:
            try:
                telegram.send(message)
            except telegram.TelegramUncertainError:
                # **실패 알림은 모름이어도 한 번 더 보낸다** (docs/infra.md 25.618, 교차검증). 25.613 이 보낸 뒤 끊긴
                # 연결을
                # "모름" 으로 바꾸자 실패 알림도 재시도를 잃어, 끊김 한 번이면 로그만 남고 알림이 사라졌다.
                # 실패 알림은 두 번 가도 해가 없다 — 리포트 본문과 다르다
                log.warning("실패 알림 발송 여부를 몰라 한 번 더 보냅니다")
                time.sleep(5)
                telegram.send(message)
    except Exception:  # noqa: BLE001 — 알림 실패가 배치의 종료코드를 덮으면 진짜 원인이 가려진다
        log.error("실패 알림을 보내지 못했습니다\n%s", traceback.format_exc())


def main() -> int:
    parser = argparse.ArgumentParser(description="일일 배치")
    parser.add_argument("--market", required=True, choices=["KR", "US", "kr", "us"])
    parser.add_argument(
        "--force", action="store_true", help="예약 시각 판단을 건너뛰고 실행한다"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="텔레그램으로 보내지 않고 출력만 한다"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=config.SETTINGS.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    try:
        return run(args.market, force=args.force, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        reason = db.quota_reason(exc)
        if reason:
            # 한도는 고장이 아니다. 기록을 쓸 수도 없으니(쓰기가 막혔다) 알림만 한 번 보내고
            # 실패로 끝내지 않는다. 알림은 DB 를 타지 않는다 (docs/infra.md 25.6)
            _notify_quota(args.market.upper(), reason, dry_run=args.dry_run)
            # **연 기록을 닫는다** (docs/infra.md 25.447). 이 경로는 `guard()` 에 닿지 않아 바깥 `daily_*` 와 안에서 연
            # 하위 기록이 `running` 으로 남았다 — /status 수동 실행 단추가 여섯 시간 "(실행 중)" 으로 막혔다.
            # 읽기 한도면 닫는 UPDATE 는 된다. 쓰기 한도면 막히고, 그때는 `reap_stale_runs()` 가 닫는다(두 겹)
            while db.open_run_depth():
                db.close_current_run("skipped", f"DB 한도로 건너뜀: {reason}")
            print(f"건너뜀: {reason}")
            return 0
        log.error("배치가 예기치 않게 중단됐습니다\n%s", traceback.format_exc())
        print(f"중단: {exc}")
        return 1


def _notify_quota(market: str, reason: str, dry_run: bool) -> None:
    """DB 한도로 오늘 배치를 건너뛴다고 알린다. 알림 실패가 배치 결과를 덮지 않는다."""
    label = "국내" if market == "KR" else "미국"
    message = (
        f"⏸ {label} 일일 배치를 건너뜁니다\n"
        f"{reason}\n"
        # "오늘 리포트는 나가지 않습니다" 라고 **단정하지 않는다** (25.568, 교차검증) — 앞선 실행이 리포트를
        # 보낸 뒤 한도에 걸려 기록을 못 닫으면(25.567) 다음 트리거가 여기로 와, 이미 받은 리포트와 반대 말을
        # 했다. 한도라 확인도 못 한다
        "이 실행은 리포트를 보내지 않았습니다. 오늘 리포트를 이미 받았다면 그것이 오늘 리포트입니다.\n"
        "한도가 풀리면 다음 예약 시각에 다시 돕니다."
    )
    try:
        if dry_run:
            print("--- 한도 알림 (dry-run) ---")
            print(message)
        else:
            telegram.send(message)
    except Exception:  # noqa: BLE001 — 위와 같다. 알림 실패가 한도 판정을 덮으면 안 된다
        log.error("한도 알림을 보내지 못했습니다\n%s", traceback.format_exc())


if __name__ == "__main__":
    sys.exit(guard(main))
