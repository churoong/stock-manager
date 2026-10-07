"""팩터와 종합 점수 계산·적재.

계산식은 docs/factors.md, 구현은 batch/services/scoring.py 에 있다.
이 파일은 입력을 읽어 넘기고 결과를 저장하는 일만 한다.

**입력이 없으면 점수를 만들지 않는다.** 미국은 재무 수집 경로가 아직 없어
밸류·퀄리티·성장이 통째로 빈다. 그래서 종합 점수가 나오지 않는다.
이것은 버그가 아니라 데이터 상태이고, skip_reason 에 남아 화면에 드러난다.

실행
  python -m batch.jobs.scores --market KR
  python -m batch.jobs.scores --market KR --as-of 2026-09-15
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core import settings_range as sr
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import metrics as mt
from batch.services import scoring as sc
from batch.services import sentiment as sentiment_svc
from batch.sources import dart

log = logging.getLogger("scores")

JOB_NAME = "scores"

# 재무는 사업보고서 연결 기준만 쓴다. 분기를 섞으면 배수가 왜곡된다.
#: 정의처는 `batch/sources/dart.ANNUAL_REPORT_CODE` 하나다 (docs/infra.md 25.143)
ANNUAL_REPORT_CODE = dart.ANNUAL_REPORT_CODE

# 이익 안정성을 볼 연수.
STABILITY_YEARS = 5

# 리스크 팩터가 볼 창. **정의처는 `services/metrics.RISK_WINDOWS` 다** —
# `jobs/signals` 도 같은 것을 보고, 예전에는 양쪽이 따로 적고 있었다(docs/infra.md 25.98).
RISK_WINDOWS = mt.RISK_WINDOWS

DEFAULT_WEIGHTS = {
    "value": 20.0,
    "quality": 20.0,
    "growth": 20.0,
    "momentum": 20.0,
    "risk": 20.0,
}


# ----------------------------------------------------------------------
# 입력 읽기
# ----------------------------------------------------------------------


def load_universe(client: TursoClient, country: str, as_of: str) -> list[dict[str, Any]]:
    """`as_of` 시점에 **알 수 있었던** 스냅샷의 편입 종목. 제외 종목은 점수를 내지 않는다.

    **`as_of` 를 반드시 건다** (2026-09-21, docs/infra.md 25.97). 예전에는 늘 최신
    스냅샷을 읽었다. 그러면 과거 기준일로 다시 계산할 때 그때는 알 수 없던
    **편입 여부와 `market_cap`** 을 쓴다 — 시가총액은 밸류 팩터 넷(E/P·B/P·S/P·D/P)의
    **분모**라, 6월 점수의 싼 정도를 9월 시가총액으로 재게 된다.

    가정이 아니다. `.github/workflows/scores.yml` 이 `--as-of` 를 입력으로 받고,
    `jobs/daily.py` 는 **전 거래일**을 기준일로 넘긴다(25.92 와 같은 모양).
    """
    rs = client.execute(
        "SELECT s.id AS stock_id, s.ticker, s.market, s.sector, u.market_cap, s.currency,"
        # 밸류 분모 시점 정합 (docs/factors.md 3.1, 25.954) — 시총의 가격 날짜와 스냅샷 날짜. 추가 읽기 0 행
        "  u.snapshot_date, s.market_cap_date"
        " FROM universe_members u JOIN stocks s ON s.id = u.stock_id"
        " WHERE u.included = 1 AND s.country = ?"
        f"   AND u.snapshot_date = {db.snapshot_as_of_sql()}",
        [country, country, as_of],
    )
    return rs.dicts()


def load_financials(client: TursoClient, country: str, as_of: str) -> dict[int, dict[int, dict]]:
    """`as_of` 까지 **공시된** 연간 재무. {stock_id: {fiscal_year: row}}

    **발표일(`report_date`)을 건다** (2026-09-21, docs/infra.md 25.97).
    사업연도로만 고르면 **2025 사업보고서를 2026-01-01 에 이미 아는 것**이 된다 —
    실제 접수는 2026-03 무렵이다. 밸류·퀄리티·성장 셋이 통째로 미래를 본다.

    `report_date` 는 `NOT NULL` 이라(migrations/0005) 걸러서 잃는 행이 없다.
    같은 규칙을 `jobs/valuation_bands.py:60` 과 `services/signals.known_equity_at` 이
    **이미 쓰고 있었다** — 이 자리만 빠져 있었다.

    **알고 쓰는 한계 하나.** 유니크 키가 `(stock_id, fiscal_year, report_code, consolidated)`
    라(migrations/0005) 정정 공시가 오면 **같은 행을 덮는다** — 원래 발표일이 남지 않는다.
    3월에 내고 5월에 정정한 사업보고서는 `report_date` 가 5월이 되어 **4월 기준 점수에서는
    아예 빠진다.** 그 방향이 맞다 — 빠지는 것은 결측이고, 안 빠지면 **정정된 숫자를 정정
    전에 아는** look-ahead 다. 시점 스냅샷이 필요한 곳(백테스트)은 `financial_snapshots`
    를 본다.
    """
    rs = client.execute(
        # **기준(연결·별도)은 종목마다 한 번만 고른다** (docs/infra.md 25.890). 예전에는 재무 행마다 같은 종목의
        # 기준 고르기를 다시 돌려 나라 전체 질의 하나가 재무 표를 7배쯤 읽었다(인구 DB 실측 47만 → 14만 행).
        # `MATERIALIZED` 가 없으면 SQLite 가 펼쳐 예전과 같아진다. 결과는 같다(`tests/test_financial_basis_890.py`)
        "WITH b AS MATERIALIZED (SELECT s.id AS sid, (SELECT fb.consolidated FROM financials fb"
        " WHERE fb.stock_id = s.id AND fb.report_code = ? AND fb.report_date <= ?"
        " ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1) AS cons FROM stocks s WHERE s.country = ?)"
        " SELECT f.stock_id, f.fiscal_year, f.net_income, f.total_equity,"
        "       f.total_assets, f.total_liabilities, f.revenue, f.operating_income,"
        # 2026-09-17: 유동비율·피오트로스키 축소판·자산 성장률 (docs/factors.md 10.2)
        "       f.current_assets, f.current_liabilities, f.noncurrent_liabilities,"
        "       f.report_date, f.currency"
        " FROM b CROSS JOIN financials f ON f.stock_id = b.sid AND f.consolidated = b.cons"
        " WHERE f.report_code = ? AND f.report_date <= ?"
        " ORDER BY f.stock_id, f.report_date",
        [ANNUAL_REPORT_CODE, as_of, country, ANNUAL_REPORT_CODE, as_of],  # 기준 고르기(25.856)·본 질의
    )
    out: dict[int, dict[int, dict]] = {}
    for row in rs.dicts():
        out.setdefault(int(row["stock_id"]), {})[int(row["fiscal_year"])] = row
    return out


def drop_stale_annual(financials: dict[int, dict[int, dict]], as_of: str) -> dict[int, str]:
    """최신 사업보고서가 `STALE_ANNUAL_DAYS` 넘게 묵은 종목의 연간 재무를 **비운다**.

    docs/infra.md 25.804, 감사 — 25.660 에서 남긴 점수 쪽. 신호의 중기 성장률은 25.660 에서 묵은 보고서를
    쓰지 않게 했는데, 점수는 감사 지연·의견거절로 새 사업보고서가 없는 회사의 2년 묵은 순이익·자본으로
    지금 가격과 E/P·B/P·ROE 를 내 밸류·퀄리티·성장을 매겼다. 비우면 그 팩터들이 결측이 되어 문서의 결측
    규칙대로 간다(팩터 둘 이상이 비면 종합 점수 없음). {종목: 사유} 를 돌려주어 실행 기록에 남긴다.
    백테스트(`pit_financials`)도 같은 문턱이다.
    """
    사유: dict[int, str] = {}
    for sid, years in list(financials.items()):
        if not years:
            continue
        latest = max(years)
        접수 = years[latest].get("report_date")
        묵음 = sc.annual_stale_reason(None if 접수 is None else str(접수), as_of, int(latest))
        if 묵음:
            사유[sid] = 묵음
            financials[sid] = {}
    return 사유


# 감성 점수가 이보다 오래됐으면 쓰지 않는다. 뉴스 수집이 멈췄는데 옛 분위기로 점수를 움직이지 않게.
# **정의처는 `services.sentiment.MAX_AGE_DAYS` 다** — 매도 플래그도 같은 잣대를 써야 해서
# 2026-09-23 에 한곳으로 모았다 (docs/infra.md 25.161)
SENTIMENT_MAX_AGE_DAYS = sentiment_svc.MAX_AGE_DAYS


def load_sentiments(
    client: TursoClient, country: str, as_of: str
) -> tuple[dict[int, float], str | None]:
    """종목별 최신 감성(−100~+100)과 **못 읽었으면 그 이유**.

    기준일 이전 3일 안의 값만 쓴다. 표가 없거나 값이 없으면 빈 dict 이고, 그때는
    `total_score` 가 5팩터로 재정규화한다(CLAUDE.md).

    **"아직 없다" 와 "못 읽었다" 를 가른다** (docs/infra.md 25.164). 전에는 어떤
    실패든 빈 dict 이었다 — 한도에 걸린 날에도 **온 시장의 점수가 센티먼트 없이**
    계산되고 아무 데도 그 사실이 남지 않았다. 빈 값이 숫자를 바꾸는 자리다.
    """
    try:
        rs = client.execute(
            "SELECT ss.stock_id, ss.sentiment FROM sentiment_scores ss JOIN stocks s ON s.id = ss.stock_id"
            " WHERE s.country = ? AND ss.sentiment IS NOT NULL"
            "   AND ss.as_of_date <= ? AND ss.as_of_date >= date(?, ?) AND ss.as_of_date = ("
            "   SELECT MAX(x.as_of_date) FROM sentiment_scores x WHERE x.stock_id = ss.stock_id"
            "   AND x.as_of_date <= ? AND x.as_of_date >= date(?, ?))",
            [country, as_of, as_of, f"-{SENTIMENT_MAX_AGE_DAYS} days", as_of, as_of, f"-{SENTIMENT_MAX_AGE_DAYS} days"],
        )
    except Exception as e:  # noqa: BLE001 — 표가 없는 DB 는 정상, 나머지는 말한다
        if db.표가_없나(e):
            return {}, None
        return {}, str(e)
    return {int(r[0]): float(r[1]) for r in rs.rows}, None


def load_metrics(client: TursoClient, country: str, as_of: str) -> dict[int, dict]:
    """`as_of` 까지 계산된 성과 지표. 창은 3Y 를 먼저 보고 없으면 1Y 로 내려간다.

    **`as_of` 를 건다** (2026-09-21, docs/infra.md 25.97). 리스크 팩터 열 개 중 **일곱**이
    이 표에서 온다. 안 걸면 과거 기준일 점수가 **오늘까지의 가격으로 낸 변동성·MDD·샤프**를
    쓴다 — 리스크 축이 통째로 미래를 보는 것이다.
    """
    rs = client.execute(
        "SELECT m.stock_id, m.window, m.as_of_date, m.calc_version, m.cagr, m.mdd,"
        "       m.mdd_recovery_days, m.mdd_trough_date, m.volatility_ann, m.sharpe, m.sortino, m.beta"
        f" FROM (WITH w(win) AS (VALUES {', '.join(['(?)'] * len(RISK_WINDOWS))}) SELECT win FROM w) w"
        " CROSS JOIN stocks s CROSS JOIN performance_metrics m"
        " WHERE s.country = ? AND m.id = (SELECT x.id FROM performance_metrics x WHERE x.stock_id = s.id"
        "   AND x.window = w.win AND x.as_of_date <= ? ORDER BY x.as_of_date DESC, x.calc_version DESC LIMIT 1)",
        [*RISK_WINDOWS, country, as_of],
    )

    # 창 고르기는 `services/metrics.pick_window` 한 곳에 있다 (docs/infra.md 25.98)
    return mt.pick_window(rs.dicts())


# ----------------------------------------------------------------------
# `momentum_reference_dates` 와 `load_reference_closes` 는 여기 있었다 (2026-09-21 삭제)
# ----------------------------------------------------------------------
# 시장 전체 거래일 목록에서 다섯 자리(0·21·63·126·273)의 **날짜**를 골라, 그 날짜의
# 종가만 따로 읽어 모멘텀 기준점으로 썼다. 질의 둘이 그 일을 했다.
#
# **지운 이유는 두 가지다.**
#
# 1. **같은 실행 안에서 답이 둘이었다.** `momentum_12_1` 은 날짜로 기준점을 잡는데,
#    `momentum_vol_adjusted` 의 12-1 분자는 `load_series` 가 준 **종목 자신의 계열**을
#    뒤에서 세어 잡는다. 거래일에 구멍이 있는 종목은 한 실행·한 종목·같은 창에서
#    두 값이 갈렸다. 백테스트(`build_pit_inputs`)는 늘 계열 쪽이라 셋이 어긋났다
#    (docs/infra.md 25.100)
# 2. **읽을 필요가 없었다.** `load_series` 가 이미 274 거래일을 읽어 오고,
#    다섯 기준점은 그 안에 **전부 들어 있다**(`max(MOMENTUM_OFFSETS) + 1 == 274`).
#    질의 둘이 이미 가진 것을 다시 읽고 있었다.
#
# 지금은 `sc.momentum_metrics(closes_series)` 하나가 한다 — 백테스트와 **같은 함수**다.

#: 계열을 읽을 때 더 거슬러 가는 거래일 수 (25.556). 창 안 정지일이 이만큼까지는 그 종목 자신의 거래일로 창을 채운다.
#: 한 달 반(30거래일) — 그보다 긴 정지는 25.100 대로 NULL 이 된다. 읽는 행은 약 11% 늘어난다
SERIES_GAP_ALLOWANCE = 30


#: 지수 일봉의 마지막 날이 기준일보다 이만큼 넘게 앞서면 지수 달력을 믿지 않는다(지수 수집이 멈췄다)
#: — 날짜 걷기로 돌아간다
INDEX_CALENDAR_MAX_LAG_DAYS = 7


def market_dates_from_index(client: TursoClient, country: str, as_of: str, n: int) -> list[str] | None:
    """기준일 이하 최근 n 거래일 — **그 나라 벤치마크 지수의 일봉 날짜**로 (docs/infra.md 25.899). 못 믿으면 None.

    예전에는 시세 표를 날짜 색인으로 거슬러 걸었다(25.865 의 재귀 CTE). 그런데 날짜 색인 안에서 한 날짜의 행은
    **먼저 저장된 순서**라 국내 시세(아침)가 미국 시세(밤)보다 앞에 온다. 미국 날짜를 찾을 때마다 그날 국내 행
    약 2,800개를 지나쳐 **334걸음에 152만 행**을 읽었다(2026-10-02 운영 실측, `db-status` 의 "서버가 훑은 행").
    미국 점수 하루 301만 행 중 258만이 이 계열 읽기였다. 지수 일봉은 나라마다 하루 한 행이라 n 행만 읽는다.

    날짜는 `since`(가장 이른 날)를 정하는 데만 쓴다 — 지수가 하루 빠져도 창이 하루 길어질 뿐이다(종목마다 끝 days
    행만 남긴다). 지수 표가 없거나 지수가 모자라거나(n 미만) 마지막 날이 기준일보다
    `INDEX_CALENDAR_MAX_LAG_DAYS` 넘게 앞서면 None — 부르는 쪽이 예전 걷기로 간다.
    """
    from batch.jobs.metrics import BENCHMARK

    코드 = BENCHMARK.get(country)
    if not 코드:
        return None
    try:
        rs = client.execute(
            "SELECT date FROM index_prices WHERE index_code = ? AND date <= ? ORDER BY date DESC LIMIT ?",
            [코드, as_of, n],
        )
    except Exception as exc:
        # 표가 없을 때(마이그레이션 전 DB)만 예전 걷기로 간다. 한도·인증 실패는 걷기도 실패한다 — 삼키지 않는다(25.164)
        if not db.표가_없나(exc):
            raise
        log.warning("지수 표가 없어 시세 날짜를 걸어 셉니다: %s", exc)
        return None
    dates = [str(r[0])[:10] for r in rs.rows]
    if len(dates) < n:
        return None
    from datetime import date as _date

    if (_date.fromisoformat(as_of[:10]) - _date.fromisoformat(dates[0])).days > INDEX_CALENDAR_MAX_LAG_DAYS:
        return None
    return dates


def load_series(
    client: TursoClient, country: str, as_of: str, days: int,
    moves: dict[int, dict[str, float | None]] | None = None,
) -> dict[int, tuple[list[str], list[float], list[float | None]]]:
    """최근 days 거래일의 (수정)종가와 거래대금 계열. {stock_id: (closes, values)}.

    2026-09-17 에 더한 지표(52주 고점·꾸준함·변동성 조정·Amihud, docs/factors.md 10.1)는
    기준점 몇 개가 아니라 계열 전체가 필요하다. 시장 전체 거래일 목록에서 days 번째
    날짜를 잡아 그 뒤만 읽는다. 종목 900개 × 274일 ≈ 25만 행이다.

    거래대금은 저장된 value 를 쓰고, 없으면 원 종가 × 거래량으로 낸다.
    """
    # **구멍 난 종목이 제 거래일로 창을 채우게 여유를 두고 읽고, 종목마다 끝 days 행만 남긴다** (docs/infra.md 25.556,
    # 감사 재현). 시장 날짜 days 번째부터만 읽어 정지일이 하루라도 있는 종목은 273행이 되고, 12-1·꾸준함·변동성 조정
    # 모멘텀이 NULL 이 됐다 — 문서(factors.md 3장 "구멍만큼 먼 과거까지 간다")와 백테스트(전 이력)와 달랐다
    dates = market_dates_from_index(client, country, as_of, days + SERIES_GAP_ALLOWANCE)
    if dates is None:
        rs = client.execute(
            "WITH RECURSIVE d(x) AS ("
            " SELECT (SELECT MAX(p.date) FROM prices p JOIN stocks s ON s.id = p.stock_id"
            "   WHERE s.country = ? AND p.date <= ?)"
            " UNION ALL SELECT (SELECT MAX(p.date) FROM prices p JOIN stocks s ON s.id = p.stock_id"
            "   WHERE s.country = ? AND p.date < d.x) FROM d WHERE d.x IS NOT NULL LIMIT ?)"
            " SELECT x FROM d WHERE x IS NOT NULL ORDER BY x DESC",
            [country, as_of, country, days + SERIES_GAP_ALLOWANCE],
        )
        dates = [str(row[0]) for row in rs.rows]
    if not dates:
        return {}
    since = dates[-1]

    rs = client.execute(
        "SELECT p.stock_id, p.date, COALESCE(p.adj_close, p.close) AS px, p.value, p.close, p.volume, p.change_pct"
        " FROM universe_members u CROSS JOIN stocks s CROSS JOIN prices p"
        " WHERE u.included = 1 AND u.snapshot_date = (SELECT MAX(um.snapshot_date) FROM universe_members um"
        "   JOIN stocks su ON su.id = um.stock_id WHERE su.country = ? AND um.snapshot_date <= ?)"
        "  AND s.id = u.stock_id AND s.country = ?"
        "  AND p.stock_id = u.stock_id AND p.date >= ? AND p.date <= ? AND p.close IS NOT NULL"
        " ORDER BY p.stock_id, p.date",
        [country, as_of, country, since, as_of],
    )
    out: dict[int, tuple[list[str], list[float], list[float | None]]] = {}
    for row in rs.dicts():
        dates, closes, values = out.setdefault(int(row["stock_id"]), ([], [], []))
        # **날짜도 들고 온다** (2026-09-21). 질의는 그대로다 — `p.date` 를 이미 고르면서
        # 버리고 있었다. 잔차 변동성(docs/factors.md 11.3)이 지수와 **날짜를 맞춰야** 한다.
        # 날짜를 안 맞추면 값이 통째로 틀린다(베타와 같은 규칙)
        dates.append(str(row["date"]))
        closes.append(float(row["px"]))
        # 국내 가격 배수의 재료 (docs/factors.md 3.1, 25.954) — 같은 행에서 등락률만 더 꺼낸다. 질의는 그대로다
        if moves is not None:
            moves.setdefault(int(row["stock_id"]), {})[str(row["date"])] = (
                None if row["change_pct"] is None else float(row["change_pct"])
            )
        value = row["value"]
        if value is None and row["volume"] is not None and row["close"] is not None:
            value = float(row["close"]) * float(row["volume"])
        values.append(None if value is None else float(value))
    return {sid: (d[-days:], c[-days:], v[-days:]) for sid, (d, c, v) in out.items()}


def load_benchmark_closes(
    client: TursoClient, country: str, days: int, as_of: str, 못읽음: list[str] | None = None
) -> dict[str, float]:
    """그 나라 벤치마크 지수의 {날짜: 종가} (docs/factors.md 11.3).

    **지수 코드를 여기서 다시 적지 않는다** — `jobs/metrics.BENCHMARK` 를 가져다 쓴다.
    25.65 가 코드를 손으로 `"S&P500"` 이라고 잘못 적어 두었던 항목이다. 베타와 같은
    지수를 봐야 잔차의 뜻이 베타와 이어진다.

    **기준일 뒤의 지수는 읽지 않는다** (docs/infra.md 25.106). 종목 종가는 기준일에
    묶여 있는데 지수만 안 묶여 있었다. `aligned_returns` 가 날짜로 맞추므로 겹치는 날이
    없으면 잔차 변동성이 **통째로 빈다** — 틀린 값이 아니라 **아무 값도 안 나온다.**
    과거 기준일로 다시 계산하면 리스크 팩터의 재료 하나가 조용히 사라진다.
    """
    from batch.jobs.metrics import BENCHMARK

    코드 = BENCHMARK.get(country)
    if not 코드:
        return {}
    try:
        rs = client.execute(
            "SELECT date, close FROM index_prices WHERE index_code = ? AND date <= ?"
            " ORDER BY date DESC LIMIT ?",
            [코드, as_of, days],
        )
    except Exception as exc:  # noqa: BLE001 — 지수가 없어도 나머지 팩터는 낸다
        log.warning("지수를 읽지 못해 잔차 변동성이 빕니다: %s", exc)
        # "아직 없다" 와 "못 읽었다" 를 가른다 (docs/infra.md 25.221, 25.164 의 감성과 같다)
        if 못읽음 is not None and not db.표가_없나(exc):
            못읽음.append(f"지수(잔차 변동성): {exc}")
        return {}
    return {str(r[0]): float(r[1]) for r in rs.rows}


#: 센티먼트 가중치 기본값(%). CLAUDE.md 스코어링: "종합 점수에는 설정 가중치(기본 10%)로만 반영".
#: 2026-09-26 까지 0 이었다 — 웹 설정 화면(`DEFAULT_SETTINGS.sentiment_weight = 10`)은 10% 로 보여 주면서 배치는
#: 설정이 저장되기 전까지 감성을 빼고 계산했다 (docs/infra.md 25.230). `tests/test_settings_keys.py` 가 두 값을 댄다
DEFAULT_SENTIMENT_WEIGHT = 10.0


def load_weights(client: TursoClient) -> tuple[dict[str, float], float, list[str]]:
    """설정에서 가중치를 읽는다. 없으면 균등 배분. (가중치, 센티먼트 가중치, 경고).

    **범위 밖 값은 기본값으로 되돌리고 말한다** (docs/infra.md 25.169).

    `web/lib/settings.ts` 머리말은 "설정은 웹앱만 쓴다. 배치는 읽기만 한다. 따라서
    검증은 여기 한 곳에 둔다" 고 적는다. 그 전제가 **복구·이주 경로에서 깨진다** —
    `restore_backup` 과 `move_user_data` 는 `settings` 행을 검증 없이 써 넣는다.
    옛 백업에는 지금 범위를 벗어난 값이 들어 있을 수 있다.

    되돌리는 쪽을 골랐다. 점수를 아예 안 내면 그날 추천이 통째로 비는데, 그것은
    **설정 한 줄 때문에 치르기에는 큰 값**이다. 대신 실행 기록과 화면에 남긴다.
    """
    경고: list[str] = []

    # 깨진 JSON·null 은 `get_setting` 이 말한다 (25.778)
    stored = db.get_setting(client, "factor_weights", None, 못읽음=경고)
    weights = dict(DEFAULT_WEIGHTS)
    표 = stored if isinstance(stored, dict) else {}
    # 저장값이 있는데 칸이 든 표가 아니면(빈 표·목록·글자) 통째로 기본값이다 — 말한다 (25.767, 교차검증: 25.765 가
    # 놓친 모양)
    if stored is not None and not 표:
        경고.append("팩터 가중치 설정을 읽지 못해 기본값(각 20)으로 계산했습니다 — 설정 화면에서 다시 저장하세요")
    for key, 기본 in DEFAULT_WEIGHTS.items():
        if 표.get(key) is None:
            # 저장된 표가 있는데 칸이 빠졌으면 말한다 — 말없이 기본값(20)을 쓰면 화면("읽지 못한 칸")과 배치가 갈린다
            # (25.765)
            if 표:
                경고.append(
                    f"팩터 가중치 '{key}' 칸이 없어 기본값 {기본} 으로 계산했습니다 — 설정 화면에서 다시 저장하세요"
                )
            continue
        값, 말 = sr.범위_안(key, 표[key], 기본)
        weights[key] = float(기본 if 값 is None else 값)
        if 말:
            경고.append(말)

    # **합이 0 이면 기본값으로 되돌리고 말한다** (docs/infra.md 25.559, 감사). 하나하나는 범위 안(0~100)이라 위를
    # 통과하는데, 다섯이 모두 0 이면 전 종목이 "가중치 합 0" 으로 빠지고 실행은 success 로 끝났다. 웹은 합 100 을
    # 강제하지만 복구·이주 경로(`restore_backup`·`move_user_data`)는 검증 없이 쓴다 — 25.169 와 같은 자리다
    if sum(weights.values()) <= 0:
        경고.append(f"팩터 가중치가 모두 0 이라 기본값({DEFAULT_WEIGHTS})으로 계산했습니다 — 설정 화면에서 확인")
        weights = dict(DEFAULT_WEIGHTS)

    감성, 말 = db.get_setting_in_range(client, "sentiment_weight", DEFAULT_SENTIMENT_WEIGHT)
    if 말:
        경고.append(말)
    감성값, 말 = sr.센티먼트_가중치(float(감성 or 0.0), DEFAULT_SENTIMENT_WEIGHT)
    if 말:
        경고.append(말)
    return weights, 감성값, 경고


# ----------------------------------------------------------------------
# 입력 조립
# ----------------------------------------------------------------------


def load_dividends(
    client: TursoClient, country: str, as_of: str, 못읽음: list[str] | None = None,
    years: dict[int, int] | None = None,
) -> dict[int, float]:
    """`as_of` 시점에 **알 수 있었던** 최신 사업연도의 현금배당총액 (docs/factors.md 11.1).

    **사전에 있으면 아는 것, 없으면 모르는 것이다.** 무배당은 0 으로 들어오고,
    수집이 안 닿은 종목은 아예 키가 없다 — 그 둘을 같게 보면 "배당 안 주는 회사" 와
    "아직 안 받아 온 회사" 가 한 값이 된다(docs/infra.md 25.74 와 같은 규칙).

    같은 사업연도를 여러 보고서가 싣는다. **가장 나중 보고서**의 값을 쓴다
    (`jobs/accumulation.py` 와 같은 방식).

    **`as_of_date` 를 반드시 건다** (2026-09-21, docs/infra.md 25.92).
    `migrations/0013_stock_dividends.sql` 이 그 열에 "접수일. **이 날부터 알 수 있었다**" 라고
    적어 두었다. 처음 쓸 때 그것을 빠뜨려 **오늘 접수된 배당이 어제 기준일 점수에** 들어갔다.
    가정이 아니다 — `jobs/daily.py` 가 `scores.run(market, as_of=trade_date)` 를 부르고
    `trade_date` 는 **전 거래일**이라, 정상 운영 경로에서 매일 그럴 수 있었다.

    **접수일을 모르는 행(`as_of_date IS NULL`)은 쓰지 않는다.** 언제부터 알 수 있었는지
    모르는 값을 과거 시점에 놓으면 그것이 곧 look-ahead 다. 그런 행이 많으면 배당수익률이
    비는데, **비는 것이 틀린 것보다 낫다**(CLAUDE.md: 없는 숫자를 만들지 않는다).
    """
    나온것: dict[int, float] = {}
    try:
        rows = client.execute(
            "SELECT d.stock_id, d.fiscal_year, d.cash_dividend_total"
            " FROM stock_dividends d"
            " JOIN stocks s ON s.id = d.stock_id"
            " JOIN (SELECT stock_id, fiscal_year, MAX(report_year) AS ry FROM stock_dividends"
            "       WHERE as_of_date IS NOT NULL AND as_of_date <= ?"
            "       GROUP BY stock_id, fiscal_year) m"
            "   ON m.stock_id = d.stock_id AND m.fiscal_year = d.fiscal_year AND m.ry = d.report_year"
            " WHERE s.country = ? AND d.as_of_date IS NOT NULL AND d.as_of_date <= ?",
            [as_of, country, as_of],
        ).dicts()
    except Exception as exc:  # noqa: BLE001 — 배당을 못 읽어도 나머지 팩터는 낸다
        log.warning("배당을 읽지 못해 배당수익률이 빕니다: %s", exc)
        # **온 시장의 배당수익률이 한꺼번에 빠지면 밸류 팩터가 재정규화되어 그날 순위가 바뀐다** (25.221)
        if 못읽음 is not None and not db.표가_없나(exc):
            못읽음.append(f"배당(배당수익률): {exc}")
        return {}

    최신: dict[int, int] = {}
    for r in rows:
        sid, fy = int(r["stock_id"]), int(r["fiscal_year"])
        if fy < 최신.get(sid, -1):
            continue
        최신[sid] = fy
        # None(그 해에 배당 항목이 비었다)은 **0 으로 본다** — 행이 있다는 것은 살펴봤다는 뜻이다
        나온것[sid] = float(r["cash_dividend_total"] or 0)
    if years is not None:
        years.update(최신)
    return 나온것


def fill_us_no_dividend(
    dividends: dict[int, float], div_years: dict[int, int], financials: dict[int, dict[int, dict]], as_of: str
) -> int:
    """미국: 10-K 가 있는데 그해 배당 행이 없으면 **무배당(0)** 으로 채운다 (docs/infra.md 25.557·25.561).

    규칙은 `services/scoring.us_dividend_or_zero` 하나다(백테스트와 같다). 10-K 는 **제출일이 기준일보다 앞선 것만**
    센다 — 배당 행은 제출 다음 거래일부터 보여, 제출 당일에는 재무만 보이고 배당은 안 보여 배당주가 0 이 됐다(25.561).
    채운 종목 수를 돌려준다. **알고 두는 것**: 10-K 에 배당 태그를 다른 이름으로 단 회사는 무배당으로 읽힌다 [확인필요]
    """
    채움 = 0
    for sid, by_year in financials.items():
        앞선 = [fy for fy, row in by_year.items() if str(row.get("report_date") or "") < as_of]
        if not 앞선:
            continue
        값 = sc.us_dividend_or_zero(dividends.get(sid), div_years.get(sid), max(앞선))
        if 값 == 0.0 and dividends.get(sid) != 0.0:
            dividends[sid] = 0.0
            div_years[sid] = max(앞선)
            채움 += 1
    return 채움

def attach_unrecovered_rows(client: TursoClient, metrics: dict[int, dict], as_of: str) -> None:
    """미회복 종목마다 바닥 뒤 시세 행 수를 `unrecovered_rows` 로 붙인다 (docs/infra.md 25.693, 교차검증).

    점수 계열(`load_series`)은 끝 274행뿐인데 3년 MDD 의 바닥은 750행 전일 수 있다 — 계열로 세면 하한이 274 에서 멈춰,
    같은 종목의 백테스트(750행 계열)와 순위가 뒤집혔다. `prices` 에서 바닥 다음 날부터 점수 기준일까지 센다. 행의 조건은
    성과 지표가 회복 기간을 셀 때(`jobs/metrics.load_prices`: 수정·원 종가 중 하나라도 있는 행)와 같다.
    한 종목에 한 문장이지만 `batch` 로 묶어 왕복은 묶음마다 한 번이다.
    """
    대상 = [
        (sid, str(row["mdd_trough_date"]))
        for sid, row in metrics.items()
        if row.get("mdd_recovery_days") is None and row.get("mdd_trough_date")
    ]
    for start in range(0, len(대상), 100):
        묶음 = 대상[start : start + 100]
        결과 = client.batch([
            (
                "SELECT COUNT(*) FROM prices WHERE stock_id = ? AND date > ? AND date <= ?"
                " AND COALESCE(adj_close, close) IS NOT NULL",
                [sid, 바닥, as_of],
            )
            for sid, 바닥 in 묶음
        ])
        for (sid, _), rs in zip(묶음, 결과, strict=True):
            metrics[sid]["unrecovered_rows"] = float(rs.rows[0][0]) if rs.rows else None


def build_inputs(
    universe: list[dict[str, Any]],
    financials: dict[int, dict[int, dict]],
    metrics: dict[int, dict],
    series: dict[int, tuple[list[str], list[float], list[float | None]]] | None = None,
    dividends: dict[int, float] | None = None,
    benchmark_closes: dict[str, float] | None = None,
    *,
    as_of: str | None = None,
    moves: dict[int, dict[str, float | None]] | None = None,
    pending_adjust: set[int] | None = None,
) -> list[sc.StockInput]:
    """종목마다 지표 묶음을 만든다. 없는 입력은 없는 채로 둔다.

    series 는 계열이 필요한 지표(docs/factors.md 10.1)용이다. 없으면 그 지표들만 비고
    나머지는 그대로다.
    `as_of`·`moves`·`pending_adjust` 는 밸류 분모 시점 정합(docs/factors.md 3.1, 25.954)의 재료 — 스냅샷 시총을 기준일
    가격으로 옮긴다. `as_of` 가 없으면(옛 호출부·테스트) 옮기지 않고 그대로 쓴다.
    """
    inputs: list[sc.StockInput] = []
    series = series or {}
    dividends = dividends or {}
    benchmark_closes = benchmark_closes or {}
    moves = moves or {}
    pending_adjust = pending_adjust or set()

    for row in universe:
        stock_id = int(row["stock_id"])
        years = financials.get(stock_id, {})
        latest_year = max(years) if years else None
        current = years.get(latest_year) if latest_year is not None else None
        previous = years.get(latest_year - 1) if latest_year is not None else None
        # **표시통화가 바뀐 해는 견주지 않는다** (docs/infra.md 25.919, 11회차) — 두산밥캣은 2023 부터 연결을 USD 로 내
        # 3년 매출 CAGR(2025 달러 ÷ 2022 원)이 뜻을 잃었다. 통화가 다른 해는 없는 해로 본다
        if previous is not None and not sc.same_currency(current, previous):
            previous = None
        # 중간 해가 비면 3년 CAGR 을 내지 않는다 (docs/infra.md 25.308)
        three_ago = (
            years.get(latest_year - 3)
            if latest_year is not None and sc.has_cagr_years(years, latest_year)
            else None
        )
        if three_ago is not None and not sc.same_currency(current, three_ago):
            three_ago = None

        market_cap, cap_note = scaled_market_cap(row, stock_id, as_of, series, moves, pending_adjust)
        # **재무 통화가 시가총액 통화와 다르면 밸류 비율을 내지 않는다** (docs/infra.md 25.915, 감사). 두산밥캣은
        # 연결재무를 USD 로
        # 공시해(DART `currency`, 25.729 는 기록만) 순이익 2.8억 달러가 "2.8억원" 으로 원화 시총에 나뉘어 E/P·B/P·S/P
        # 가 약 1/1,400 —
        # 밸류 점수가 업종 바닥이었다. 환산할 기준 환율을 정하기 전까지는 모름이다. ROE·마진·성장률 같은 비율은 통화와
        # 무관해 그대로 쓴다
        통화다름 = bool(current and current.get("currency") and row.get("currency")
                     and current["currency"] != row["currency"])  # fmt: skip
        밸류재무 = None if 통화다름 else current

        merged: dict[str, float | None] = {}
        merged.update(
            sc.value_metrics(
                net_income=_num(밸류재무, "net_income"),
                total_equity=_num(밸류재무, "total_equity"),
                revenue=_num(밸류재무, "revenue"),
                market_cap=None if market_cap is None else float(market_cap),
            )
        )
        # 배당수익률 (docs/factors.md 11.1). **사전에 없으면 None** — 무배당(0)과 다르다
        merged["dividend_yield"] = sc.dividend_yield(
            dividends.get(stock_id),
            None if market_cap is None else float(market_cap),
        )

        profitable, observed = _stability_counts(years, latest_year)
        merged.update(
            sc.quality_metrics(
                net_income=_num(current, "net_income"),
                total_assets=_num(current, "total_assets"),
                total_equity=_num(current, "total_equity"),
                operating_income=_num(current, "operating_income"),
                revenue=_num(current, "revenue"),
                total_liabilities=_num(current, "total_liabilities"),
                profitable_years=profitable,
                observed_years=observed,
            )
        )
        merged.update(
            sc.growth_metrics(
                revenue=_num(current, "revenue"),
                prev_revenue=_num(previous, "revenue"),
                operating_income=_num(current, "operating_income"),
                prev_operating_income=_num(previous, "operating_income"),
                revenue_3y_ago=_num(three_ago, "revenue"),
                prev_total_assets=_num(previous, "total_assets"),
            )
        )
        # 재무제표 지표. 백테스트(jobs/backtest.build_pit_inputs)와 같은 함수를 부른다
        merged.update(sc.statement_metrics(current, previous))

        # 모멘텀·계열 지표. 백테스트(jobs/backtest.build_pit_inputs)와 **같은 함수**를 부르고
        # **같은 계열**을 넘긴다. 예전에는 모멘텀만 날짜로 기준점을 잡아, 같은 실행 안에서
        # `momentum_12_1` 과 `momentum_vol_adjusted` 의 12-1 이 갈렸다 (docs/infra.md 25.100)
        dates_series, closes_series, values_series = series.get(stock_id, ([], [], []))
        merged.update(sc.momentum_metrics(closes_series))
        merged.update(sc.price_series_metrics(closes_series, values_series or None, dates_series))

        # 잔차 변동성 (docs/factors.md 11.3). 지수 계열이 더 필요해 위 묶음에 없다.
        # **날짜를 맞추는 일은 여기서 한다** — 맞추지 않으면 값이 통째로 틀린다(베타와 같다)
        merged["idio_volatility"] = sc.idio_volatility(
            *sc.aligned_returns(dict(zip(dates_series, closes_series, strict=True)), benchmark_closes)
        )

        metric_row = metrics.get(stock_id)
        merged.update(
            sc.risk_metrics(
                mdd=_num(metric_row, "mdd"),
                volatility_ann=_num(metric_row, "volatility_ann"),
                sharpe=_num(metric_row, "sharpe"),
                sortino=_num(metric_row, "sortino"),
                beta=_num(metric_row, "beta"),
                mdd_recovery_days=(
                    None
                    if metric_row is None or metric_row.get("mdd_recovery_days") is None
                    else int(metric_row["mdd_recovery_days"])
                ),
                cagr_value=_num(metric_row, "cagr"),
                # 바닥 뒤 지난 행 수 — 미회복 회복 기간의 하한 (25.691). 백테스트와 같은 함수
                # `attach_unrecovered_rows` 가 붙인 값(prices 전체에서 센 것)이 먼저, 없으면 점수 계열로 (25.693)
                unrecovered_rows=(
                    metric_row["unrecovered_rows"]
                    if metric_row is not None and metric_row.get("unrecovered_rows") is not None
                    else sc.elapsed_rows_since(
                        dates_series, None if metric_row is None else metric_row.get("mdd_trough_date")
                    )
                ),
            )
        )

        inputs.append(
            sc.StockInput(
                stock_id=stock_id,
                market=str(row["market"]),
                sector=row.get("sector") or None,
                metrics=merged,
                # 어느 창·기준일의 성과 지표를 썼는지 (docs/infra.md 25.383)
                risk_source=(
                    None
                    if metric_row is None
                    else {"window": metric_row.get("window"), "as_of_date": metric_row.get("as_of_date")}
                ),
                market_cap_note=cap_note,
            )
        )
    return inputs


def scaled_market_cap(
    row: dict[str, Any],
    stock_id: int,
    as_of: str | None,
    series: dict[int, tuple[list[str], list[float], list[float | None]]],
    moves: dict[int, dict[str, float | None]],
    pending_adjust: set[int],
) -> tuple[float | None, dict[str, Any] | None]:
    """스냅샷 시총을 기준일 가격으로 옮긴다 (docs/factors.md 3.1 "분모 시점 규칙", infra 25.954).

    국내는 등락률 곱(`price_factor_kr`), 미국은 수정종가 비율(`price_factor_us`) — 미국은 시총 날짜가 일일 재수집 창
    (`US_LOOKBACK_DAYS`, 10 달력일) 밖이거나 수정주가 재수집 대기 중이면 옮기지 않는다(수정종가 기준이 어긋날 수 있다).
    `as_of` 가 없으면 기록 없이 그대로(옛 호출부).
    """
    cap = row.get("market_cap")
    cap_f = None if cap is None else float(cap)
    if as_of is None:
        return cap_f, None
    cap_date = row.get("market_cap_date")
    snapshot_date = row.get("snapshot_date")
    dates, closes, _values = series.get(stock_id, ([], [], []))
    국내 = str(row.get("market") or "") in ("KOSPI", "KOSDAQ")
    factor: float | None = None
    reason: str | None = None
    # 배수는 시총의 가격 날짜부터 낸다. 옮길지 말지(날짜 순서·14일·띠)는 `scale_market_cap` 이 정한다 (25.960)
    if cap_f is not None and cap_date and snapshot_date and str(cap_date)[:10] <= str(snapshot_date)[:10]:
        if 국내:
            factor = sc.price_factor_kr(dates, moves.get(stock_id, {}), str(cap_date), as_of)
        else:
            if stock_id in pending_adjust:
                reason = "수정주가 재수집 대기 중"
            elif (date.fromisoformat(as_of) - date.fromisoformat(str(cap_date)[:10])).days > US_ADJ_WINDOW_DAYS:
                reason = f"시총 날짜가 재수집 창({US_ADJ_WINDOW_DAYS}일) 밖"
            else:
                factor = sc.price_factor_us(dates, closes, str(cap_date), as_of)
    scaled, note = sc.scale_market_cap(cap_f, factor, cap_date=cap_date, snapshot_date=snapshot_date, reason=reason)
    return scaled, note


#: 미국 일일 적재가 수정종가를 새 기준으로 덮어 쓰는 창(달력일) — `jobs/daily.US_LOOKBACK_DAYS` 기본값과 같다. 이 창
#: 밖의 시총 날짜는 수정종가 기준이 어긋날 수 있어 옮기지 않는다 (docs/factors.md 3.1, 13회차 검증 B)
US_ADJ_WINDOW_DAYS = 10


def load_pending_adjust(client: TursoClient) -> set[int]:
    """미국 수정주가 재수집 대기 종목 (docs/factors.md 3.1). 표가 없으면 빈 집합 — 그 종목들은 옮기지 않는다."""
    try:
        rs = client.execute("SELECT DISTINCT stock_id FROM adjust_refresh_queue WHERE done_at IS NULL")
    except Exception as exc:  # noqa: BLE001
        if db.표가_없나(exc):
            return set()
        raise
    return {int(r[0]) for r in rs.rows}


def _num(row: dict | None, key: str) -> float | None:
    if row is None:
        return None
    value = row.get(key)
    return None if value is None else float(value)


def _stability_counts(
    years: dict[int, dict], latest_year: int | None
) -> tuple[int | None, int | None]:
    """최근 몇 개 사업연도 중 영업흑자 연수를 센다."""
    if latest_year is None:
        return None, None

    window = [
        years[y]
        for y in range(latest_year - STABILITY_YEARS + 1, latest_year + 1)
        if y in years and years[y].get("operating_income") is not None
    ]
    if not window:
        return None, None
    profitable = sum(1 for row in window if float(row["operating_income"]) > 0)
    return profitable, len(window)


# ----------------------------------------------------------------------
# 적재
# ----------------------------------------------------------------------

_FACTOR_COLS = (
    "stock_id, as_of_date, factor, raw_json, zscore, score, peer_group,"
    " peer_size, missing_fields, calc_version, created_at"
)

_SCORE_COLS = (
    "stock_id, as_of_date, total_score, factor_scores, sentiment_score,"
    " sentiment_weight_used, weights_json, rank_in_market, rank_in_sector,"
    " skip_reason, calc_version, created_at"
)


def _bulk(
    client: TursoClient, table: str, columns: str, conflict: str, rows: list[tuple]
) -> int:
    """여러 행을 한 문장에 넣는다. 열 개수는 목록에서 직접 센다.

    같은 키가 있으면 **덮어쓴다**. 2026-09-17 까지는 DO NOTHING 이라 같은 기준일로 다시 계산해도
    첫 실행 값이 남았다. 성과 지표가 비어 있던 9/16 첫 실행의 리스크 점수(전 종목 NULL)가 백필 뒤
    재계산에도 그대로 남은 사고가 있었다(docs/infra.md 17절). 가중치 변경처럼 뜻이 바뀌는 재계산은
    calc_version 을 올린다(docs/factors.md 5장) — 그 원칙은 그대로다.
    """
    if not rows:
        return 0

    param_count = db.column_count(columns)
    for row in rows:
        if len(row) != param_count:
            raise ValueError(
                f"값 {len(row)}개인데 열은 {param_count}개입니다. "
                "열을 더하거나 뺄 때 값 묶음도 함께 고쳐야 합니다"
            )

    keys = {c.strip() for c in conflict.split(",")}
    update_cols = [c.strip() for c in columns.split(",") if c.strip() and c.strip() not in keys]
    placeholder = "(" + ", ".join(["?"] * param_count) + ")"
    per_statement = max(1, 20_000 // param_count)

    statements: list[tuple[str, list[Any]]] = []
    for start in range(0, len(rows), per_statement):
        chunk = rows[start : start + per_statement]
        sql = (
            f"INSERT INTO {table} ({columns}) VALUES "
            + ", ".join([placeholder] * len(chunk))
            + f" ON CONFLICT ({conflict}) DO UPDATE SET "
            + ", ".join(f"{c} = excluded.{c}" for c in update_cols)
        )
        args: list[Any] = []
        for row in chunk:
            args.extend(row)
        statements.append((sql, args))

    client.batch(statements)
    return len(rows)


def clear_statement(table: str, country: str, as_of: str, calc_version: int) -> tuple[str, list]:
    """그 나라·그 기준일·그 계산식 행을 **넣기 전에** 모두 지우는 문장.

    왜 지우고 새로 넣나: upsert 만 하면 유니버스에서 빠진 종목의 옛 점수가 남는다
    (docs/infra.md 17절). 예전에는 "남길 종목 목록에 없는 것"을 지웠는데 목록이 종목 수만큼
    파라미터가 되어 D1 한도(질의당 100개)를 넘었다. 시각(created_at)으로 가르는 방법도 써 봤지만
    시계에 기대는 판정이라 버렸다 — 같은 초 안에 두 번 돌면 아무것도 지우지 않는다.

    **지운 뒤 넣는 사이에 죽으면 그날 행이 비어 있게 된다.** 점수·신호는 다시 계산하면 되는 값이라
    이 위험을 받아들인다. 사람이 넣은 값에는 이 방식을 쓰지 않는다.
    """
    return (
        f"DELETE FROM {table} WHERE as_of_date = ? AND calc_version = ?"
        " AND stock_id IN (SELECT id FROM stocks WHERE country = ?)",
        [as_of, calc_version, country],
    )


def run(market: str, as_of: str | None = None) -> int:
    market = market.upper()
    country = "KR" if market == "KR" else "US"
    client = TursoClient()

    try:
        db.apply_migrations(client)
        as_of = as_of or cal.default_as_of(market)  # 직전 거래일 (docs/infra.md 25.234)
        run_id = db.start_batch_run(
            client, job_name=JOB_NAME, market=market, trade_date=as_of
        )

        단계 = db.ReadSteps()  # 단계별 읽은 행 (25.898)
        universe = load_universe(client, country, as_of)
        단계.mark("universe")
        if not universe:
            db.finish_batch_run(
                client, run_id, status="failed", error_text="유니버스가 비어 있습니다"
            )
            print("유니버스가 비어 있습니다. python -m batch.jobs.universe 를 먼저 돌리세요")
            return 1

        등락률: dict[int, dict[str, float | None]] = {}
        series = load_series(client, country, as_of, max(sc.MOMENTUM_OFFSETS) + 1, moves=등락률)
        단계.mark("series")
        재료_못읽음: list[str] = []
        financials = load_financials(client, country, as_of)
        단계.mark("financials")
        묵은_재무 = drop_stale_annual(financials, as_of)
        # 배당 (docs/factors.md 11.1). 2026-09-21 까지 점수는 배당을 한 번도 안 봤다
        배당연도: dict[int, int] = {}
        못읽음_전 = len(재료_못읽음)
        dividends = load_dividends(client, country, as_of, 재료_못읽음, 배당연도)
        # 못 읽었거나 미국 배당 행이 하나도 없으면(표가 없거나 수집 전) 0 으로 채우지 않는다 — 모름이다
        if country == "US" and len(재료_못읽음) == 못읽음_전 and 배당연도:
            fill_us_no_dividend(dividends, 배당연도, financials, as_of)
        단계.mark("dividends")
        성과지표 = load_metrics(client, country, as_of)
        단계.mark("metrics")
        attach_unrecovered_rows(client, 성과지표, as_of)
        단계.mark("unrecovered_rows")
        inputs = build_inputs(
            universe,
            financials,
            성과지표,
            series,
            dividends,
            # 잔차 변동성의 지수 계열 (docs/factors.md 11.3). 세 줄짜리 표라 싸다
            load_benchmark_closes(client, country, max(sc.MOMENTUM_OFFSETS) + 1, as_of, 재료_못읽음),
            # 밸류 분모 시점 정합 (docs/factors.md 3.1, 25.954)
            as_of=as_of,
            moves=등락률,
            pending_adjust=load_pending_adjust(client) if country == "US" else set(),
        )
        # 몇 종목을 옮겼고 왜 못 옮겼는지 실행 기록에 (25.954)
        옮김 = [i.market_cap_note for i in inputs if i.market_cap_note]
        사유별: dict[str, int] = {}
        for n in 옮김:
            if not n["scaled"]:
                사유별[str(n["reason"])] = 사유별.get(str(n["reason"]), 0) + 1
        시총옮김 = {"scaled": sum(1 for n in 옮김 if n["scaled"]), "reasons": 사유별}

        단계.mark("benchmark")
        weights, sentiment_weight, 설정경고 = load_weights(client)
        factor_results = sc.score_factors(inputs)
        now = db.now_iso()

        factor_rows = [
            (
                r.stock_id,
                as_of,
                r.factor,
                json.dumps(r.raw, ensure_ascii=False),
                r.zscore,
                r.score,
                r.peer_group,
                r.peer_size,
                json.dumps(r.missing_fields, ensure_ascii=False),
                sc.CALC_VERSION,
                now,
            )
            for r in factor_results
        ]

        by_stock: dict[int, dict[str, float | None]] = {}
        groups: dict[int, str] = {}
        for r in factor_results:
            by_stock.setdefault(r.stock_id, {})[r.factor] = r.score
            groups[r.stock_id] = r.peer_group

        markets = {int(row["stock_id"]): str(row["market"]) for row in universe}
        sentiments, 감성_못읽음 = load_sentiments(client, country, as_of)
        단계.mark("weights_sentiment")
        totals: dict[int, sc.TotalScore] = {
            stock_id: sc.total_score(
                scores, weights, sentiment=sentiments.get(stock_id), sentiment_weight=sentiment_weight
            )
            for stock_id, scores in by_stock.items()
        }

        market_ranks = _ranks_by(totals, markets)
        sector_ranks = _ranks_by(totals, groups)
        # 시장 집단으로 올라간 종목의 "집단 내 순위" 는 **시장 순위**다 (docs/infra.md 25.307).
        # 올라간 종목끼리만 매기면 z 를 낸 표본(시장 전체)과 순위를 낸 표본이 달라진다
        for stock_id, group in groups.items():
            if group.startswith("market:") and stock_id in market_ranks:
                sector_ranks[stock_id] = market_ranks[stock_id]

        score_rows = [
            (
                stock_id,
                as_of,
                total.total,
                json.dumps(by_stock[stock_id], ensure_ascii=False),
                # **종합 점수에 실제로 들어간 센티먼트.** 2026-09-21 까지 여기에 None 을 넣고
                # "센티먼트는 Step 7 에서 붙는다" 고 적어 두었는데, 붙이는 코드는 없었다.
                # 그동안 총점은 센티먼트를 섞어 내면서 그 값은 어디에도 남지 않았다 —
                # 화면이 분리해 보여 줄 수 없고, 매수 스냅샷 sentiment_at_trade 도 늘 비었다
                # (docs/infra.md 25.66)
                total.sentiment_used,
                total.sentiment_weight_used,
                json.dumps(
                    total.weights_used or weights, ensure_ascii=False
                ),
                market_ranks.get(stock_id),
                sector_ranks.get(stock_id),
                total.skip_reason,
                sc.CALC_VERSION,
                now,
            )
            for stock_id, total in totals.items()
        ]

        # 넣기 전에 지운다. 지운 뒤 넣는 순서라 유니버스에서 빠진 종목의 옛 행이 남지 않는다.
        # 계산을 끝까지 마친 뒤에만 여기 온다(도중에 실패하면 지우지 않는다)
        단계.mark("compute")
        if totals:
            client.batch([clear_statement(table, country, as_of, sc.CALC_VERSION) for table in ("factors", "scores")])
        _bulk(
            client,
            "factors",
            _FACTOR_COLS,
            "stock_id, as_of_date, factor, calc_version",
            factor_rows,
        )
        _bulk(
            client,
            "scores",
            _SCORE_COLS,
            "stock_id, as_of_date, calc_version",
            score_rows,
        )

        scored = sum(1 for t in totals.values() if t.total is not None)
        skipped = len(totals) - scored
        단계.mark("write")
        db.finish_batch_run(
            client,
            run_id,
            status="success",
            step_log={
                "reads_by_step": 단계.steps,
                "universe": len(universe),
                "factor_rows": len(factor_rows),
                "scored": scored,
                "skipped": skipped,
                # 밸류 분모를 기준일 가격으로 옮긴 종목 수와 못 옮긴 사유 (docs/factors.md 3.1, 25.954)
                "market_cap_scaled": 시총옮김,
                # 빈 감성이 "아직 없다" 인지 "못 읽었다" 인지 남긴다 (docs/infra.md 25.164)
                **({"sentiment_read_error": 감성_못읽음} if 감성_못읽음 else {}),
                # 배당·지수도 같다 (docs/infra.md 25.221)
                **({"input_read_errors": 재료_못읽음} if 재료_못읽음 else {}),
                # 묵은 사업보고서라 연간 재무를 쓰지 않은 종목 (docs/infra.md 25.804)
                **({"stale_annual": 묵은_재무} if 묵은_재무 else {}),
                # 범위 밖 가중치를 기본값으로 되돌렸으면 남긴다 (docs/infra.md 25.169)
                **({"setting_warnings": 설정경고} if 설정경고 else {}),
            },
        )

        print(f"대상 {len(universe)}종목")
        print(f"  팩터 행 {len(factor_rows)}")
        print(f"  종합 점수 {scored}종목, 산출 불가 {skipped}종목")
        for 줄 in 설정경고:
            print(f"  주의: {줄}")
        for 줄 in 재료_못읽음:
            print(f"  주의: 온 시장에서 이 재료 없이 계산했습니다 — {줄}")
        if 감성_못읽음:
            # **온 시장이 센티먼트 없이 계산됐다.** 조용히 넘어가면 그날 점수가 왜 달라졌는지 모른다
            print(f"  주의: 감성을 읽지 못해 5팩터로 재정규화했습니다 — {감성_못읽음}")
        # 가장 긴 계열조차 12-1 창(274 거래일)에 못 미치면 **모든 종목**의 긴 모멘텀이 빈다.
        # 예전에는 시장 거래일 목록(`reference_dates`)으로 봤는데, 그 목록을 지우면서
        # 같은 것을 계열 길이로 본다 (2026-09-21, docs/infra.md 25.100)
        가장긴계열 = max((len(c) for _d, c, _v in series.values()), default=0)
        if 가장긴계열 <= max(sc.MOMENTUM_OFFSETS):
            print(f"  주의: 거래일이 {가장긴계열}일뿐이라 일부 모멘텀 지표가 비었습니다")
        return 0
    finally:
        client.close()


def _ranks_by(
    totals: dict[int, sc.TotalScore], grouping: dict[int, str]
) -> dict[int, int]:
    """집단별로 순위를 매긴다. 집단이 다르면 서로 섞이지 않는다."""
    buckets: dict[str, list[tuple[int, float | None]]] = {}
    for stock_id, total in totals.items():
        key = grouping.get(stock_id, "")
        buckets.setdefault(key, []).append((stock_id, total.total))

    out: dict[int, int] = {}
    for members in buckets.values():
        out.update(sc.rank_within(members))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="팩터와 종합 점수 계산")
    parser.add_argument("--market", required=True, choices=["KR", "US", "kr", "us"])
    parser.add_argument("--as-of", dest="as_of", help="기준일 YYYY-MM-DD")
    args = parser.parse_args()

    logging.basicConfig(
        level=config.SETTINGS.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    return run(args.market, args.as_of)


if __name__ == "__main__":
    sys.exit(guard(main))
