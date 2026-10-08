"""매수 신호 계산과 적재.

규칙은 docs/signals.md, 구현은 batch/services/signals.py 에 있다.
이 파일은 입력을 읽어 넘기고 결과를 저장하는 일만 한다.

**자동 매매는 없다.** 여기서 만드는 것은 화면과 텔레그램이 읽을 정보뿐이고,
주문을 내는 코드는 이 저장소에 존재하지 않는다.

조회를 두 단계로 나눈다.
  1단계  최근 60 거래일만 전 종목에 대해 읽는다. 이동평균과 수급에 쓴다
  2단계  장기 후보(퀄리티·밸류가 문턱을 넘은 소수)만 3년치를 더 읽는다

밸류에이션 밴드에는 3년치가 필요한데, 유니버스 전체에 3년치를 읽으면 행이
수십만이 된다. 대부분은 어차피 문턱에서 걸러지므로 살아남은 종목에만
비싼 조회를 한다.

실행
  python -m batch.jobs.signals --market KR
  python -m batch.jobs.signals --market KR --as-of 2026-09-15
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import UTC, datetime
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core import settings_range as sr
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import fx, insider, trend
from batch.services import metrics as mt
from batch.services import scoring as scoring_svc
from batch.services import signals as sg
from batch.sources import dart

log = logging.getLogger("signals")

JOB_NAME = "signals"

#: 정의처는 `batch/sources/dart.ANNUAL_REPORT_CODE` 하나다 (docs/infra.md 25.143)
ANNUAL_REPORT_CODE = dart.ANNUAL_REPORT_CODE

# 1단계에서 읽을 거래일 수. 60일 이동평균에 필요한 만큼이다.
RECENT_DAYS = sg.MA_LONG_DAYS

# 2단계에서 읽을 거래일 수. 3년치다.
# **숫자를 여기 다시 적지 않는다** (2026-09-22, docs/infra.md 25.108).
# 같은 750 이 세 곳에 따로 있었다 — 여기·services/valuation_band·jobs/backtest.
BAND_DAYS = sg.BAND_DAYS

# 밴드를 계산할 종목 수 상한.
# 장기 후보가 지나치게 많으면 조회가 길어진다. 점수 높은 순으로 자른다.
MAX_BAND_STOCKS = 300

DEFAULT_MIN_ORDER = 100_000.0


# ----------------------------------------------------------------------
# 입력 읽기
# ----------------------------------------------------------------------


def load_candidates(client: TursoClient, country: str, as_of: str) -> list[dict]:
    """유니버스 편입 종목과 그 팩터 점수.

    점수가 없는 종목은 신호 판정의 입력이 모자라므로 빼지 않고 그대로 둔다.
    기간별 규칙이 각자 필요한 값이 없으면 알아서 신호를 내지 않는다.
    """
    rs = client.execute(
        "SELECT s.id AS stock_id, s.ticker, s.market, s.sector, s.currency,"
        "       s.listed_shares, COALESCE(s.name_ko, s.name_en) AS name,"
        "       sc.factor_scores, sc.total_score, sc.as_of_date AS score_date"
        " FROM universe_members u"
        " JOIN stocks s ON s.id = u.stock_id"
        # **계산 판(calc_version)까지 봐야 한 행이다** (2026-09-21, docs/infra.md 25.91).
        # `scores` 의 유니크 키는 (stock_id, as_of_date, **calc_version**) 이고 적재는
        # **같은 판만 지운다**(jobs/scores.clear_statement). 그래서 계산식을 올린 날
        # (CALC_VERSION 3→4)에는 같은 기준일에 두 행이 남고, 이 조인이 한 종목을 **두 번**
        # 돌려줬다. 날짜 고르는 규칙은 그대로 두고 **판만 한 겹 더** 건다 —
        # 그래야 각 자리가 원래 보던 날짜 규칙을 잃지 않는다
        # **기준일 이하의 점수만 본다** (2026-09-21, docs/infra.md 25.98).
        # 예전에는 `as_of` 를 인자로 받고도 질의에 한 번도 안 걸었다 — 6월 기준 신호가
        # 9월 점수로 판정됐다. 유니버스 스냅샷도 마찬가지다(25.97 과 같은 모양)
        " LEFT JOIN scores sc"
        "   ON sc.stock_id = s.id"
        "  AND sc.as_of_date = (SELECT MAX(as_of_date) FROM scores"
        "                       WHERE stock_id = s.id AND as_of_date <= ?)"
        "  AND sc.calc_version = (SELECT MAX(c.calc_version) FROM scores c"
        "                         WHERE c.stock_id = sc.stock_id AND c.as_of_date = sc.as_of_date)"
        " WHERE u.included = 1 AND s.country = ?"
        f"   AND u.snapshot_date = {db.snapshot_as_of_sql()}",
        [as_of, country, country, as_of],
    )
    rows = rs.dicts()
    for row in rows:
        row["scores"] = _decode_scores(row.get("factor_scores"))
    return rows


def _decode_scores(raw: Any) -> dict[str, float | None]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def load_recent_prices(
    client: TursoClient, country: str, as_of: str, days: int = RECENT_DAYS
) -> dict[int, list[tuple[str, float, float | None]]]:
    """최근 N 거래일의 (날짜, 종가, 거래대금). 날짜 오름차순.

    **수정주가를 쓴다**(docs/adjust.md 7장). 이동평균과 이격도는 여러 날을 잇는 값이라
    분할이 하루 끼면 그 뒤 60일이 통째로 틀어진다. 거래대금은 조정하지 않는다 —
    "그날 얼마가 오갔나" 는 그날의 실제 금액이다.
    """
    # **유니버스 종목 번호와 시세를 나눠 읽는다** (docs/infra.md 25.1033) — 시세 질의가 `prices`·`json_each` 만 써서
    # 시세 사본(25.888)이 있으면 거기서 읽는다. 예전엔 `universe_members` 조인 때문에 날마다 Turso 에서 읽었다
    from batch.jobs import scores as sj

    ids = sorted(int(r[0]) for r in client.execute(sj.SERIES_IDS_SQL, [country, country, as_of]).rows)
    if not ids:
        return {}
    return load_recent_prices_for(client, ids, as_of, days)


#: `load_recent_prices` 와 같은 모양을 **고른 종목만** — 유니버스 밖 참고 판정표 (`jobs/analyze_extra`, 25.1019)
RECENT_PRICES_FOR_SQL = (
    # 하위 질의를 `j.value`(종목 번호)에 건다 — `p.stock_id` 에 걸면 같은 표라 날짜 범위를 색인에 못 쓰고
    # 종목의 전 이력을 훑었다(질의 계획: date<? 만). 이 모양은 date>? AND date<? 범위로 찾는다 (docs/infra.md 25.1033)
    "SELECT p.stock_id, p.date, COALESCE(p.adj_close, p.close) AS close, p.value"
    " FROM json_each(?) j JOIN prices p ON p.stock_id = j.value WHERE p.date <= ?"
    "  AND p.date >= COALESCE((SELECT x.date FROM prices x WHERE x.stock_id = j.value AND x.date <= ?"
    "    ORDER BY x.date DESC LIMIT 1 OFFSET ? - 1), '')"
    " ORDER BY p.stock_id, p.date"
)


def load_recent_prices_for(
    client: TursoClient, stock_ids: list[int], as_of: str, days: int = RECENT_DAYS
) -> dict[int, list[tuple[str, float, float | None]]]:
    """`load_recent_prices` 와 같은 것을 고른 종목만."""
    out: dict[int, list[tuple[str, float, float | None]]] = {}
    for row in client.execute(RECENT_PRICES_FOR_SQL, [json.dumps(stock_ids), as_of, as_of, days]).dicts():
        if row["close"] is not None:
            out.setdefault(int(row["stock_id"]), []).append(
                (str(row["date"]), float(row["close"]), _opt_float(row["value"])))
    return out


#: 정의처는 `services/signals.STALE_ANNUAL_DAYS` (25.660)
STALE_ANNUAL_DAYS = sg.STALE_ANNUAL_DAYS


def load_growth(client: TursoClient, country: str, as_of: str, stock_ids: list[int] | None = None) -> dict[int, dict]:
    """최근 연간 재무와 전년도. 성장률을 여기서 낸다.

    **접수일이 기준일 뒤인 보고서는 읽지 않는다** (docs/infra.md 25.106).
    이 값이 중기 신호(실적 모멘텀)의 재료다 — 과거 기준일로 다시 계산할 때 아직 나오지도
    않은 실적으로 "모멘텀이 좋다" 고 판정하면 백테스트도 복기도 뜻을 잃는다.
    `financials.report_date` 는 회계연도 말일이 아니라 **접수일**이다(0005 마이그레이션).

    `stock_ids` 를 주면 그 종목만 읽는다 — 유니버스 밖 종목 참고 판정표(`jobs/analyze_extra`, 25.1019)
    """
    골라 = None if stock_ids is None else json.dumps(stock_ids)
    rs = client.execute(
        # **기준(연결·별도)은 종목마다 한 번만 고른다** (docs/infra.md 25.890). 예전에는 재무 행마다 같은 종목의
        # 기준 고르기를 다시 돌려 나라 전체 질의 하나가 재무 표를 7배쯤 읽었다(인구 DB 실측 47만 → 14만 행).
        # `MATERIALIZED` 가 없으면 SQLite 가 펼쳐 예전과 같아진다. 결과는 같다(`tests/test_financial_basis_890.py`)
        "WITH b AS MATERIALIZED (SELECT s.id AS sid, (SELECT fb.consolidated FROM financials fb"
        " WHERE fb.stock_id = s.id AND fb.report_code = ? AND fb.report_date <= ?"
        " ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1) AS cons FROM stocks s WHERE s.country = ?"
        " AND (? IS NULL OR s.id IN (SELECT value FROM json_each(?))))"
        " SELECT f.stock_id, f.fiscal_year, f.revenue, f.operating_income, f.consolidated, f.report_date, f.currency"
        " FROM b CROSS JOIN financials f ON f.stock_id = b.sid AND f.consolidated = b.cons"
        " WHERE f.report_code = ? AND f.report_date <= ?",
        # 기준 고르기(25.856)·본 질의
        [ANNUAL_REPORT_CODE, as_of, country, 골라, 골라, ANNUAL_REPORT_CODE, as_of],
    )
    by_stock: dict[int, dict[int, dict]] = {}
    for row in rs.dicts():
        by_stock.setdefault(int(row["stock_id"]), {})[int(row["fiscal_year"])] = row

    out: dict[int, dict] = {}
    for stock_id, years in by_stock.items():
        latest = max(years)
        current, previous = years[latest], years.get(latest - 1)
        # **표시통화가 바뀐 해는 견주지 않는다** (docs/infra.md 25.919, 11회차). 두산밥캣은 2023 부터 연결을 USD 로 내
        # 2023 영업이익
        # 성장률이 −99.9%(달러 ÷ 원)였다. 전년 통화가 다르면 전년이 없는 것과 같다
        if previous is not None and not scoring_svc.same_currency(current, previous):
            previous = None
        # **묵은 사업보고서로 "실적 모멘텀" 을 내지 않는다** (docs/infra.md 25.660, 감사). 감사 지연·의견거절로
        # 최신 사업보고서가 없는 회사가 재작년 성장률로 중기 신호를 받았다. 접수일이 15개월(약 457일)보다 오래되면
        # 한 번의 사업보고서 주기를 건너뛴 것이라 성장률을 비운다 — 판정표에는 값 없음으로 떨어진다
        # 정정공시가 접수일을 새로 덮는 구멍(25.663)까지 한 함수가 본다 — 점수·백테스트와 같은 정의 (25.808)
        접수 = str(current.get("report_date") or "")[:10]
        묵음 = sg.annual_stale_reason(접수 or None, as_of, latest)
        if 묵음:
            out[stock_id] = {"fiscal_year": latest, "consolidated": bool(current.get("consolidated", 1)),
                             "revenue_growth": None, "operating_income_growth": None, "stale_report_date": 접수,
                             "stale_reason": 묵음}
            continue
        out[stock_id] = {
            "fiscal_year": latest,
            # 연결이 없어 별도를 썼는지 — 근거 문장이 "사업보고서(별도)" 로 밝힌다 (docs/infra.md 25.314)
            "consolidated": bool(current.get("consolidated", 1)),
            "revenue_growth": _growth(
                _opt_float(current.get("revenue")),
                _opt_float(previous.get("revenue")) if previous else None,
            ),
            "operating_income_growth": _growth(
                _opt_float(current.get("operating_income")),
                _opt_float(previous.get("operating_income")) if previous else None,
            ),
        }
    return out


def _growth(current: float | None, previous: float | None) -> float | None:
    """전년이 0 이하면 성장률이 뜻을 잃는다. services/scoring.py 와 같은 규칙."""
    if current is None or previous is None or previous <= 0:
        return None
    return current / previous - 1


def load_metrics(client: TursoClient, country: str, as_of: str, stock_ids: list[int] | None = None) -> dict[int, dict]:
    """비중 축소에 쓸 변동성과 MDD. 창 고르기는 `services/metrics.pick_window` 가 한다.

    **`as_of` 를 건다** (2026-09-21, docs/infra.md 25.98). 안 걸면 과거 기준일 신호가
    오늘까지의 가격으로 낸 변동성·MDD 로 비중을 줄인다.

    고르는 규칙을 여기 다시 적지 않는다. 2026-09-21 까지 이 함수는
    `jobs/scores.load_metrics` 와 **같은 일을 따로** 하고 있었고, 그래서 한쪽을 고쳐도
    다른 쪽은 그대로였다 — **빈 3Y 행이 값이 든 1Y 행을 이기는** 결함이 여기 남아 있었다.

    `cagr` 를 함께 읽는 이유: `pick_window` 가 "값이 들었나" 를 `cagr`·`mdd` 로 본다
    (`jobs/metrics.py` 가 표본 부족을 세는 것과 같은 판정이다). 안 읽으면 늘 "빈 행" 으로
    보여 창 고르기가 뒤집힌다.

    **창 목록도 `RISK_WINDOWS` 에서 만든다** (2026-09-23, docs/infra.md 25.176).
    25.98 에서 "고르는 규칙" 은 한 곳으로 합쳤는데 **"어떤 창을 읽을까" 는 여기 `('3Y',
    '1Y')` 로 박혀 남아 있었다.** 규칙 하나가 반만 합쳐진 것이다.
    """
    rs = client.execute(
        # beta 는 종목 분석의 예상 주가(CAPM)가 쓴다 (docs/analysis.md 10.1, 25.1024)
        "SELECT m.stock_id, m.window, m.as_of_date, m.calc_version, m.volatility_ann, m.mdd, m.cagr, m.sharpe, m.beta"
        f" FROM (WITH w(win) AS (VALUES {', '.join(['(?)'] * len(mt.RISK_WINDOWS))}) SELECT win FROM w) w"
        " CROSS JOIN stocks s CROSS JOIN performance_metrics m"
        " WHERE s.country = ? AND (? IS NULL OR s.id IN (SELECT value FROM json_each(?)))"
        " AND m.id = (SELECT x.id FROM performance_metrics x WHERE x.stock_id = s.id"
        "   AND x.window = w.win AND x.as_of_date <= ? ORDER BY x.as_of_date DESC, x.calc_version DESC LIMIT 1)",
        [*mt.RISK_WINDOWS, country, *[None if stock_ids is None else json.dumps(stock_ids)] * 2, as_of],
    )
    return mt.pick_window(rs.dicts())


def load_band(
    client: TursoClient, stock_id: int, as_of: str, listed_shares: int | None
) -> sg.Band | None:
    """한 종목의 밸류에이션 밴드. 3년치 가격과 발표일 기준 자본을 쓴다."""
    if not listed_shares or listed_shares <= 0:
        return None

    # 분할만 반영한 가격을 쓴다. PBR = 시총/자본인데, 시총을 "그 가격 × 지금 주식수" 로 잡으면
    # 분할·감자가 있어도 앞뒤가 한 잣대가 된다 (docs/adjust.md 7장). 미국 Adj Close 는 배당까지
    # 담아 과거 PBR 을 낮춘다 — 미국은 야후 Close 다 (docs/infra.md 25.213, `db.SPLIT_ONLY_PRICE_SQL`)
    prices_rs = client.execute(
        f"SELECT p.date, {db.SPLIT_ONLY_PRICE_SQL} AS close FROM prices p JOIN stocks s ON s.id = p.stock_id"
        " WHERE p.stock_id = ? AND p.date <= ? ORDER BY p.date DESC LIMIT ?",
        [stock_id, as_of, BAND_DAYS],
    )
    prices = [
        (str(row["date"]), float(row["close"]))
        for row in reversed(prices_rs.dicts())
        if row["close"] is not None
    ]
    if not prices:
        return None

    equity_rs = client.execute(
        "SELECT report_date, total_equity, fiscal_year FROM financials"
        " WHERE stock_id = ? AND report_code = ? AND financials.consolidated ="
        " (SELECT fb.consolidated FROM financials fb"
        " WHERE fb.stock_id = financials.stock_id AND fb.report_code = financials.report_code"
        " AND fb.report_date <= ?"
        " ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1)"
        "   AND total_equity IS NOT NULL AND report_date <= ?"
        # 시총과 나눌 자본은 **종목 통화로 낸 것만** (25.915) — USD 로 공시한 국내 회사의 PBR 이 1,400배 틀렸다
        "   AND financials.currency = (SELECT st.currency FROM stocks st WHERE st.id = financials.stock_id)"
        " ORDER BY report_date",
        # 기준 고르기도 기준일까지 — `_latest_equity` 와 같은 기준이어야 밴드와 견준다 (25.861, 25.856 이 빠뜨림)
        [stock_id, ANNUAL_REPORT_CODE, as_of, as_of],
    )
    equities = [
        (str(row["report_date"]), float(row["total_equity"]), int(row["fiscal_year"]))
        for row in equity_rs.dicts()
    ]
    if not equities:
        return None

    return sg.build_band(sg.pbr_series(prices, equities, listed_shares))


def _opt_float(value: Any) -> float | None:
    """NaN·inf 는 None — 모름이다 (docs/infra.md 25.687)."""
    if value is None:
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def load_settings(client: TursoClient, currency: str = "KRW", as_of: str | None = None) -> dict:
    """사이징에 쓰는 설정. 없으면 기본값.

    금액 설정은 원화다. currency 가 USD 면 as_of 이전 최신 환율로 나눈다(docs/signals.md 3.4).
    환율이 없으면 총액을 0 으로 둬 금액을 내지 않고 fx_missing 을 켠다.

    **범위 밖 값은 기본값으로 되돌리고 `setting_warnings` 에 담는다**
    (docs/infra.md 25.170). 여기 값들은 전부 **돈에 닿는다** — 권장 금액·비중 상한·
    최소 주문 단위. `settings.ts` 의 "검증은 웹 한 곳" 전제가 복구·이주 경로에서
    깨지므로, 읽는 쪽에서 한 번 더 본다.
    """
    경고: list[str] = []

    def _담다(쌍: tuple[float | None, str | None], 기본: float) -> float:
        값, 말 = 쌍
        if 말:
            경고.append(말)
        return float(기본 if 값 is None else 값)

    total_krw = _담다(db.get_setting_in_range(client, "total_investable_amount", 0.0), 0.0)
    min_krw = _담다(
        db.get_setting_in_range(client, "min_order_amount", float(DEFAULT_MIN_ORDER)),
        float(DEFAULT_MIN_ORDER),
    )
    targets, 목표경고 = sr.기간별_목표(db.get_setting(client, "horizon_targets", None, 못읽음=경고))
    경고 += 목표경고
    종목상한 = _담다(db.get_setting_in_range(client, "max_weight_per_stock", 10.0), 10.0)
    섹터상한 = _담다(db.get_setting_in_range(client, "max_weight_per_sector", 30.0), 30.0)
    종목상한, 말 = sr.비중_상한_맞추기(종목상한, 섹터상한)
    if 말:
        경고.append(말)
    out = {
        "max_weight_per_stock": 종목상한,
        "total_investable": total_krw,
        "total_investable_krw": total_krw,
        "max_weight_per_sector": 섹터상한,
        "targets": targets,
        "min_order_amount": min_krw,
        "currency": currency,
        "fx": None,
        "fx_missing": False,
        "setting_warnings": 경고,
    }
    if currency == "KRW":
        return out

    rate = fx.latest_rate(client, as_of or datetime.now(UTC).date().isoformat())
    if rate is None:
        out.update(total_investable=0.0, fx_missing=True)
        return out
    # **`or` 로 되돌리지 않는다** (2026-09-23, docs/infra.md 25.179).
    #
    # `min_order_amount = 0` 은 **뜻 있는 값**이다 — "최소 주문 제한 없음". 그것을
    # `services/report_picks` 가 `if min_order_amount > 0 and …` 으로 스스로 증명한다.
    # 그런데 `0.0` 은 거짓이라 `or` 가 타고, 그 자리에 **원화 기본값 100,000 이
    # 달러 숫자로** 앉았다. 같은 함수가 낸 예산이 $35,714 인데 최소 주문이 $100,000 이
    # 되어 **미국 추천의 권장 금액이 전부 사라졌다.** 경고도 안 나갔다.
    #
    # 되돌릴 일이 생기면(환산 불가) 그 기본값도 **환율로 나눠야** 통화가 안 섞인다.
    환산_총액 = fx.convert_krw(total_krw, currency, rate)
    환산_최소 = fx.convert_krw(min_krw, currency, rate)
    out.update(
        total_investable=환산_총액 if 환산_총액 is not None else 0.0,
        min_order_amount=환산_최소 if 환산_최소 is not None else DEFAULT_MIN_ORDER / rate.rate,
        fx=rate.as_dict(),
        fx_note=f"{total_krw:,.0f}원 ÷ {rate.describe()}",
    )
    return out


# ----------------------------------------------------------------------
# 입력 조립
# ----------------------------------------------------------------------


def needs_band(scores: dict) -> bool:
    """장기 후보인가. 밴드 조회는 비싸므로 문턱을 먼저 본다."""
    quality = scores.get("quality")
    value = scores.get("value")
    return (
        quality is not None
        and value is not None
        and quality >= sg.MIN_QUALITY_SCORE_LONG
        and value >= sg.MIN_VALUE_SCORE_LONG
    )


def build_inputs(
    candidates: list[dict],
    prices: dict[int, list[tuple[str, float, float | None]]],
    growth: dict[int, dict],
    metrics: dict[int, dict],
    bands: dict[int, sg.Band],
    insider_summaries: dict[int, insider.InsiderSummary] | None = None,
) -> list[sg.SignalInput]:
    inputs: list[sg.SignalInput] = []
    insider_summaries = insider_summaries or {}

    for row in candidates:
        stock_id = int(row["stock_id"])
        series = prices.get(stock_id, [])
        closes = [close for _date, close, _value in series]
        turnovers = [value for _d, _c, value in series if value is not None]

        growth_row = growth.get(stock_id, {})
        metric_row = metrics.get(stock_id, {})
        band = bands.get(stock_id)

        pbr_now = None
        band_close = None
        if band is not None and closes:
            band_close = row.get("_band_close") or closes[-1]
            # 밴드를 만든 것과 **같은 가격**으로 현재 PBR 을 낸다 (docs/infra.md 25.521, 감사). 밴드는 분할만 반영한
            # 종가(미국은 야후 Close)인데 여기는 수정종가(미국은 배당까지)라, 두 값이 다른 날 밴드 30% 분위를 넘는
            # 종목에 장기 신호가 났다. 그 종가를 못 읽으면 예전처럼 수정종가
            pbr_now = _current_pbr(row, band_close)

        inputs.append(
            sg.SignalInput(
                stock_id=stock_id,
                ticker=str(row["ticker"]),
                name=str(row["name"] or row["ticker"]),
                market=str(row["market"]),
                currency=str(row.get("currency") or "KRW"),
                sector=row.get("sector") or None,
                closes=closes,
                turnovers=turnovers,
                factor_scores=row.get("scores", {}),
                revenue_growth=growth_row.get("revenue_growth"),
                operating_income_growth=growth_row.get("operating_income_growth"),
                fiscal_year=growth_row.get("fiscal_year"),
                consolidated=growth_row.get("consolidated", True),
                annual_stale=growth_row.get("stale_reason"),
                pbr_now=pbr_now,
                band_close=band_close,
                band=band,
                volatility_ann=_opt_float(metric_row.get("volatility_ann")),
                mdd=_opt_float(metric_row.get("mdd")),
                cagr=_opt_float(metric_row.get("cagr")),
                sharpe=_opt_float(metric_row.get("sharpe")),
                metrics_window=(str(metric_row["window"]) if metric_row.get("window") else None),
                insider=insider_summaries.get(stock_id),
                listed_shares=(int(row["listed_shares"]) if row.get("listed_shares") else None),
                # 근거표의 기준일. 사용자가 "언제 값인가" 를 확인할 수 있어야 한다
                price_date=series[-1][0] if series else None,
                score_date=(str(row["score_date"]) if row.get("score_date") else None),
                metrics_date=(
                    str(metric_row["as_of_date"]) if metric_row.get("as_of_date") else None
                ),
            )
        )
    return inputs


def _split_only_close(client: TursoClient, stock_id: int, as_of: str) -> float | None:
    """기준일 이전 마지막 **분할만 반영한** 종가 — 밴드와 같은 잣대 (`db.SPLIT_ONLY_PRICE_SQL`, 25.521)."""
    rs = client.execute(
        # `db.SPLIT_ONLY_PRICE_SQL` 과 같은 식 — 글자로 적어 스키마 검사가 읽을 수 있게 둔다(test_sql_schema)
        "SELECT CASE WHEN s.country = 'US' THEN p.close ELSE COALESCE(p.adj_close, p.close) END AS close"
        " FROM prices p JOIN stocks s ON s.id = p.stock_id"
        " WHERE p.stock_id = ? AND p.date <= ? ORDER BY p.date DESC LIMIT 1",
        [stock_id, as_of],
    )
    값 = rs.rows[0][0] if rs.rows else None
    return float(값) if 값 else None


def _current_pbr(row: dict, close: float) -> float | None:
    equity = row.get("_latest_equity")
    shares = row.get("listed_shares")
    if not equity or not shares or shares <= 0 or equity <= 0:
        return None
    return close / (equity / shares)


# ----------------------------------------------------------------------
# 적재
# ----------------------------------------------------------------------

_COLS = (
    "stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high,"
    " currency, tranche_plan, target_price, stop_price, suggested_weight_pct,"
    " suggested_amount, size_reduction, sector_cap_applied, sector_cap_note,"
    " rationale_text, rationale_data, calc_version, created_at"
)


def to_row(signal: sg.Signal, as_of: str, now: str) -> tuple:
    return (
        signal.stock_id,
        as_of,
        signal.horizon,
        signal.signal_type,
        signal.buy_zone_low,
        signal.buy_zone_high,
        signal.currency,
        json.dumps(
            [
                {
                    "step": t.step,
                    "ratio": t.ratio,
                    "price": t.price,
                    "amount": t.amount,
                }
                for t in signal.tranches
            ],
            ensure_ascii=False,
        ),
        signal.target_price,
        signal.stop_price,
        signal.weight_pct,
        signal.suggested_amount,
        signal.size_reduction,
        1 if signal.sector_cap_applied else 0,
        signal.sector_cap_note,
        signal.rationale_text,
        json.dumps(signal.rationale_data, ensure_ascii=False, default=str),
        sg.CALC_VERSION,
        now,
    )


def store(client: TursoClient, rows: list[tuple]) -> int:
    """여러 행을 한 문장에 넣는다. 열 개수는 목록에서 직접 센다."""
    if not rows:
        return 0

    param_count = db.column_count(_COLS)
    for row in rows:
        if len(row) != param_count:
            raise ValueError(
                f"값 {len(row)}개인데 열은 {param_count}개입니다. "
                "열을 더하거나 뺄 때 값 묶음도 함께 고쳐야 합니다"
            )

    placeholder = "(" + ", ".join(["?"] * param_count) + ")"
    per_statement = max(1, 20_000 // param_count)

    statements: list[tuple[str, list[Any]]] = []
    for start in range(0, len(rows), per_statement):
        chunk = rows[start : start + per_statement]
        sql = (
            f"INSERT INTO signals ({_COLS}) VALUES "
            + ", ".join([placeholder] * len(chunk))
            + " ON CONFLICT (stock_id, as_of_date, horizon, calc_version)"
            " DO UPDATE SET"
            "   buy_zone_low = excluded.buy_zone_low,"
            "   buy_zone_high = excluded.buy_zone_high,"
            "   tranche_plan = excluded.tranche_plan,"
            "   target_price = excluded.target_price,"
            "   stop_price = excluded.stop_price,"
            "   suggested_weight_pct = excluded.suggested_weight_pct,"
            "   suggested_amount = excluded.suggested_amount,"
            "   size_reduction = excluded.size_reduction,"
            "   rationale_text = excluded.rationale_text,"
            "   rationale_data = excluded.rationale_data"
        )
        args: list[Any] = []
        for row in chunk:
            args.extend(row)
        statements.append((sql, args))

    client.batch(statements)
    return len(rows)


# 판정표 (docs/signals.md 9장). 그 나라의 최근 기준일 것만 남긴다
_CHECK_COLS = (
    "stock_id, as_of_date, horizon, passed, failed_count, checks_json, calc_version, created_at, levels_json"
)


#: 시장별 "절반 넘게 멈춤" 을 볼 만큼 큰 시장의 종목 수 (25.541). 국내 두 시장은 수백 종목이라 늘 넘는다
STALE_MARKET_MIN = 30


def too_stale(inputs: list, as_of: str) -> bool:
    """기준일 종가 없는 종목이 너무 많아 계산하지 않을 날인가 (docs/infra.md 25.526·25.531·25.535·25.541).

    나라 합계로 절반을 넘거나, **큰 시장**(`STALE_MARKET_MIN` 종목 이상) 하나가 절반을 넘으면 참.
    - 합계만 보면 코스피가 클 때 코스닥 전체 실패가 절반을 넘지 않았다(25.535)
    - 시장마다 다 보면 Cboe BZX(4종목) 같은 작은 시장 한 종목 정지로 미국 전체가 멈췄다(25.541, 1/1601 로 skipped)
    """
    if not inputs:
        return False
    시장별: dict[str, list[int]] = {}
    멈춘 = 0
    for inp in inputs:
        칸 = 시장별.setdefault(str(inp.market), [0, 0])
        칸[0] += 1
        if inp.price_date != as_of:
            칸[1] += 1
            멈춘 += 1
    return 멈춘 * 2 > len(inputs) or any(
        전체 >= STALE_MARKET_MIN and 멈 * 2 > 전체 for 전체, 멈 in 시장별.values()
    )


def check_rows(inp: sg.SignalInput, as_of: str, now: str) -> list[tuple]:
    rows: list[tuple] = []
    # 기준일 종가가 없는 종목은 **판정표에서 빼지 않고** 탈락 행으로 남긴다 (docs/infra.md 25.526, 교차검증).
    # 빼면 웹 상세가 "신호 없음" 만 보여 주고 까닭(종가 없음)을 어디에도 남기지 않았다
    멈춤 = None if inp.price_date == as_of else sg._criterion(
        "기준일 종가", f"마지막 종가 {inp.price_date or '없음'} — {as_of} 종가가 없어 신호를 내지 않는다",
        f"종가 날짜 = {as_of}", sg.SOURCE_PRICES, inp.price_date, passed=False,
    )
    # 판정표 기준을 가격으로 푼 것 — 가격 사다리 (docs/analysis.md 12.2, 25.1038)
    레벨 = sg.price_levels(inp)
    for horizon, (passed, table) in sg.judgements(inp).items():
        if 멈춤 is not None:
            table, passed = [멈춤, *table], False
        failed = sum(1 for r in table if r.get("passed") is False)
        rows.append((
            inp.stock_id, as_of, horizon, 1 if passed else 0, failed,
            json.dumps(table, ensure_ascii=False, default=str), sg.CALC_VERSION, now,
            json.dumps(레벨.get(horizon) or [], ensure_ascii=False),
        ))
    return rows


def store_checks(client: TursoClient, country: str, as_of: str, rows: list[tuple]) -> int:
    """판정표를 넣고 그 나라의 지난 기준일 것을 지운다. 표는 항상 '최근 한 번' 만 든다."""
    param_count = db.column_count(_CHECK_COLS)
    for row in rows:
        if len(row) != param_count:
            raise ValueError("signal_checks 값 묶음과 열 개수가 어긋납니다")
    placeholder = "(" + ", ".join(["?"] * param_count) + ")"
    per_statement = max(1, 20_000 // param_count)
    statements: list[tuple[str, list[Any]]] = []
    for start in range(0, len(rows), per_statement):
        chunk = rows[start : start + per_statement]
        args: list[Any] = []
        for row in chunk:
            args.extend(row)
        statements.append((
            f"INSERT INTO signal_checks ({_CHECK_COLS}) VALUES " + ", ".join([placeholder] * len(chunk))
            + " ON CONFLICT (stock_id, as_of_date, horizon) DO UPDATE SET passed = excluded.passed,"
            " failed_count = excluded.failed_count, checks_json = excluded.checks_json,"
            " calc_version = excluded.calc_version, created_at = excluded.created_at,"
            " levels_json = excluded.levels_json",
            args,
        ))
    statements.append((
        "DELETE FROM signal_checks WHERE as_of_date < ? AND stock_id IN (SELECT id FROM stocks WHERE country = ?)",
        [as_of, country],
    ))
    client.batch(statements)
    return len(rows)


def clear_statement(country: str, as_of: str) -> tuple[str, list]:
    """이번 계산에 나오지 않은 그 나라·그 기준일 신호를 지우는 문장.

    upsert 만 하면 조건이 풀린 옛 신호가 남는다. 2026-09-17 재계산에서 장기 0건이 나왔는데
    9/16 첫 실행의 장기 4건이 화면·리포트에 계속 보였다(docs/infra.md 17절).

    **넣기 전에 그 나라·그 기준일 신호를 모두 지운다.** 예전에는 남길 신호를 나열해 지웠는데
    목록이 파라미터가 되어 D1 한도(질의당 100개)를 넘었다. 지운 뒤 넣는 사이에 죽으면 그날 신호가
    비어 있게 되지만, 신호는 다시 계산하면 되는 값이라 이 위험을 받아들인다 (docs/infra.md 25.5).

    **판과 상관없이 지운다** (docs/infra.md 25.423). 예전에는 같은 판만 지워, 판을 올린 뒤 다시 계산하면
    새 판에서 사라진 기간·종목의 **옛 판 행이 살아남아** 리포트·장중 감시·웹에 계속 실렸다(25.410 을 교차검증이
    반박). 신호는 다시 계산하면 되는 값이라 판 비교 기록으로 남길 이유가 없다.
    """
    return (
        "DELETE FROM signals WHERE as_of_date = ?"
        " AND stock_id IN (SELECT id FROM stocks WHERE country = ?)",
        [as_of, country],
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

        단계 = db.ReadSteps()  # 단계별 읽은 행 (25.901 — 미국 신호 하루 약 70만 행의 출처를 가린다)
        candidates = load_candidates(client, country, as_of)
        단계.mark("candidates")
        if not candidates:
            db.finish_batch_run(
                client, run_id, status="failed", error_text="유니버스가 비어 있습니다"
            )
            print("유니버스가 비어 있습니다. 유니버스와 스코어를 먼저 돌리세요")
            return 1

        prices = load_recent_prices(client, country, as_of)
        단계.mark("prices")
        growth = load_growth(client, country, as_of)
        단계.mark("growth")
        metrics = load_metrics(client, country, as_of)
        단계.mark("metrics")

        # 장기 후보만 밴드를 만든다. 점수 높은 순으로 상한을 둔다.
        long_pool = sorted(
            (row for row in candidates if needs_band(row.get("scores", {}))),
            key=lambda r: -(r.get("total_score") or 0),
        )[:MAX_BAND_STOCKS]

        bands: dict[int, sg.Band] = {}
        for row in long_pool:
            stock_id = int(row["stock_id"])
            band = load_band(client, stock_id, as_of, row.get("listed_shares"))
            if band is not None:
                bands[stock_id] = band
                row["_latest_equity"] = _latest_equity(client, stock_id, as_of)
                row["_band_close"] = _split_only_close(client, stock_id, as_of)
        단계.mark("bands")

        settings = load_settings(client, "KRW" if country == "KR" else "USD", as_of)
        # 내부자 매매는 참고 행일 뿐이다. 표가 비어 있으면(수집기 전) 아무 행도 붙지 않는다
        insider_summaries = insider.load_summaries(client, country, as_of)
        inputs = build_inputs(candidates, prices, growth, metrics, bands, insider_summaries)
        단계.mark("settings_insider")
        # **기준일 종가가 없는 종목은 신호를 내지 않는다** (docs/signals.md 1장, docs/infra.md 25.517, 감사).
        # 최근 60행을 날짜를 보지 않고 읽어, 거래가 멈춘 종목(거래정지·상장폐지·피인수)에도 옛 종가로
        # 구간·권장 금액이 붙은 신호가 나갔다. 유니버스(주 1회)가 걸러 내기 전 며칠,
        # 미국은 유니버스가 거래정지를 보지 않아(25.129) 더 길다
        멈춘 = [inp.ticker for inp in inputs if inp.price_date != as_of]
        # 절반을 넘게 멈췄어도 계산하지 않는다 (25.531, 교차검증) — 한 시장만 수집에 실패한 날 "일부만 멈춤" 으로 돌면
        # 어제 판정표가 지워지고 거의 전부 "기준일 종가" 탈락이라 리포트가 경고 없이 "추천 없음" 이었다
        if too_stale(inputs, as_of):
            # **한 종목도 기준일 종가가 없으면 계산하지 않는다** (25.526, 교차검증). 판정표 저장이 지난 기준일 것을
            # 지우므로, 전부 빠진 채 돌면 "마지막 계산일" 이 신호가 하나라도 걸린 과거 날로 돌아가 묵은 신호가 금액과
            # 함께 되살아났다(휴장일 `--as-of`, 달력에 없는 임시공휴일, 수집 전면 실패). 어제 것을 그대로 둔다
            db.finish_batch_run(client, run_id, status="skipped", step_log={
                "reason": f"{as_of} 종가가 없는 종목이 {len(멈춘)}/{len(inputs)} 입니다(휴장일이거나 시세 수집 실패)",
                "universe": len(candidates),
            })  # fmt: skip
            print(f"{as_of} 종가가 없는 종목이 {len(멈춘)}/{len(inputs)} 이라 신호를 계산하지 않았습니다"
                  " (휴장일이거나 수집 실패)")
            return 0

        # **기준일 점수가 없으면 계산하지 않는다** (docs/infra.md 25.620, 감사). 따라잡기(d1-catchup 7단계는
        # `always()`)·단독
        # 실행에서 점수 계산이 실패해도 신호가 돌아, 기준일 이하 **가장 최근** 점수(며칠 전)로 "오늘" 신호가 나갔다 —
        # 카드의
        # 종합 점수엔 날짜가 없어 알 수 없었다. 아침 배치는 점수가 실패하면 신호를 건너뛰어 이 길만 뚫려 있었다.
        # 절반을 넘게 묵었으면 어제 판정표를 그대로 둔다(종가의 `too_stale` 과 같은 판단)
        점수_묵음 = [inp.ticker for inp in inputs if inp.score_date != as_of]
        if inputs and len(점수_묵음) * 2 > len(inputs):
            db.finish_batch_run(client, run_id, status="skipped", step_log={
                "reason": f"{as_of} 점수가 없는 종목이 {len(점수_묵음)}/{len(inputs)} 입니다"
                          "(점수 계산이 실패했거나 아직 안 돌았다)",
                "universe": len(candidates),
            })  # fmt: skip
            print(f"{as_of} 점수가 없는 종목이 {len(점수_묵음)}/{len(inputs)} 이라 신호를 계산하지 않았습니다")
            return 0

        # 시장 추세 필터 (docs/signals.md 3.5). 지수 국면은 시장마다 하나라 여기서 한 번 낸다
        trend_settings = trend.load_settings(client)
        # 추세 필터 배수가 범위 밖이면 다른 설정 경고와 함께 남긴다 (docs/infra.md 25.260)
        settings["setting_warnings"].extend(trend_settings["warnings"])
        regimes = trend.regimes_for_country(client, country, as_of)
        단계.mark("trend")

        now = db.now_iso()
        rows: list[tuple] = []
        checks: list[tuple] = []
        by_horizon = dict.fromkeys(sg.HORIZONS, 0)
        #: 근거표가 비어 내보내지 않은 신호. 있으면 안 되는 목록이다
        unverifiable: list[str] = []

        for inp in inputs:
            checks.extend(check_rows(inp, as_of, now))
            if inp.price_date != as_of:
                continue  # 판정표에는 "기준일 종가 없음" 으로 남기고 신호는 내지 않는다 (25.517·25.526)
            regime_factor, regime_data = trend.factor_for(
                regimes.get(trend.index_for_market(inp.market) or ""), trend_settings
            )
            for signal in sg.evaluate(
                inp,
                max_weight_per_stock=settings["max_weight_per_stock"],
                total_investable=settings["total_investable"],
                min_order_amount=settings["min_order_amount"],
                targets=settings["targets"],
                fx=settings["fx"],
                regime_factor=regime_factor,
                regime_data=regime_data,
            ):
                # 근거표를 만들 수 없는 추천은 내보내지 않는다 (CLAUDE.md 절대 규칙).
                # 조용히 버리지 않는다 — 이게 생기면 근거표 만드는 쪽이 고장 났다는 뜻이다
                if not sg.verifiable(signal):
                    unverifiable.append(f"{signal.ticker} {signal.horizon}")
                    continue
                rows.append(to_row(signal, as_of, now))
                by_horizon[signal.horizon] += 1

        # 넣기 전에 그 나라·그 기준일 신호를 지운다. 후보를 끝까지 평가한 뒤에만 여기 온다
        # (유니버스가 비어 일찍 끝난 실행은 지우지 않는다)
        client.batch([clear_statement(country, as_of)])
        store(client, rows)
        # 판정표는 신호와 별개로 전 종목에 남긴다 ("왜 없나" 에 답한다). 실패해도 신호는 살린다
        # 실패하면 **그 사실을** 실행 기록에 남긴다 (docs/infra.md 25.222). "판정표 0건" 만 남으면
        # "판정할 종목이 없었다" 와 구별되지 않고, "왜 없나" 화면은 지난 판정표를 그대로 보여 준다
        판정표_오류: str | None = None
        try:
            store_checks(client, country, as_of, checks)
        except Exception as exc:  # noqa: BLE001
            log.warning("판정표를 저장하지 못했습니다: %s", exc)
            checks = []
            판정표_오류 = str(exc)
        단계.mark("write")

        db.finish_batch_run(
            client,
            run_id,
            status="success",
            step_log={
                "universe": len(candidates),
                "bands": len(bands),
                "signals": len(rows),
                "by_horizon": by_horizon,
                "fx": settings["fx"],
                "fx_missing": settings["fx_missing"],
                "trend_filter": {**trend_settings, "regimes": {k: r.state for k, r in regimes.items()}},
                "insider_stocks": len(insider_summaries),
                "checks": len(checks),
                **({"checks_error": 판정표_오류} if 판정표_오류 else {}),
                "unverifiable": unverifiable,
                "reads_by_step": 단계.steps,
                # 기준일 종가가 없어 신호를 내지 않은 종목 (25.517)
                **({"stale_price": len(멈춘), "stale_price_tickers": 멈춘[:20]} if 멈춘 else {}),
                # 범위 밖 설정을 되돌렸으면 남긴다 (docs/infra.md 25.170)
                **({"setting_warnings": settings["setting_warnings"]} if settings["setting_warnings"] else {}),
            },
        )

        for 줄 in settings["setting_warnings"]:
            print(f"  주의: {줄}")
        print(f"대상 {len(candidates)}종목, 밴드 {len(bands)}종목")
        if 멈춘:
            print(f"  주의: {as_of} 종가가 없는 {len(멈춘)}종목은 신호를 내지 않았습니다 ({', '.join(멈춘[:5])} 등)")
        print(f"  {trend.report_line(regimes, trend_settings)}")
        print(f"신호 {len(rows)}건")
        for horizon in sg.HORIZONS:
            print(f"  {sg.HORIZON_LABEL[horizon]}: {by_horizon[horizon]}건")
        if unverifiable:
            # 0 이어야 정상이다. 0 이 아니면 근거표를 만드는 쪽이 고장 난 것이다
            log.warning("근거표가 없어 내보내지 않은 신호 %d건: %s", len(unverifiable), ", ".join(unverifiable))
            print(f"  주의: 근거표를 만들지 못해 {len(unverifiable)}건을 내보내지 않았습니다 (docs/infra.md 25.34)")
        if settings["fx_missing"]:
            print("  주의: 쓸 수 있는 환율(7일 이내)이 없어 미국 권장 금액을 내지 않았습니다. python -m batch.jobs.fx")
        elif settings["total_investable"] <= 0:
            print("  주의: 총 투자가능금액이 0 이라 권장 금액을 내지 않았습니다")
        return 0
    finally:
        client.close()


def _latest_equity(client: TursoClient, stock_id: int, as_of: str) -> float | None:
    """기준일에 알 수 있던 가장 최근 연간 자본총계 (docs/infra.md 25.106).

    이 값으로 **현재 PBR** 을 내고, 그것을 밸류에이션 밴드와 견준다. 밴드 쪽은 시점을
    지켰는데(`services/signals.known_equity_at`) 여기만 안 지켰다 — **견주는 두 값 중
    한쪽만 막으면 막은 뜻이 없다.** docs/signals.md 가 "미래 참조 금지" 로 약속한 자리다.

    고르는 규칙도 밴드와 같게 맞춘다 — `known_equity_at` 은 알 수 있던 것 중 **가장 늦은 사업연도**,
    같은 연도면 늦게 접수된 것(정정)을 고른다 (25.549). 접수일만 보면 옛 연도의 정정이 최신 연도를 덮었다.
    """
    rs = client.execute(
        "SELECT total_equity FROM financials"
        " WHERE stock_id = ? AND report_code = ? AND financials.consolidated ="
        " (SELECT fb.consolidated FROM financials fb"
        " WHERE fb.stock_id = financials.stock_id AND fb.report_code = financials.report_code"
        " AND fb.report_date <= ?"
        " ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1)"
        "   AND total_equity IS NOT NULL AND report_date <= ?"
        # 시총과 나눌 자본은 **종목 통화로 낸 것만** (25.915) — USD 로 공시한 국내 회사의 PBR 이 1,400배 틀렸다
        "   AND financials.currency = (SELECT st.currency FROM stocks st WHERE st.id = financials.stock_id)"
        " ORDER BY fiscal_year DESC, report_date DESC LIMIT 1",
        [stock_id, ANNUAL_REPORT_CODE, as_of, as_of],  # 기준 고르기·본 질의 (25.856)
    )
    return _opt_float(rs.scalar())


def main() -> int:
    parser = argparse.ArgumentParser(description="매수 신호 계산")
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
