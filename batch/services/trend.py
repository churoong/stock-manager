"""시장 추세 필터 (docs/signals.md 3.5). Faber (2007) 의 10개월 이동평균 규칙.

지수 종가가 200 거래일 단순이동평균 아래면 "약세 국면" 이다. 약세 국면에서는 신규 매수
신호의 권장 비중에 설정 배수(기본 0.5)를 곱한다. **점수·신호·플래그는 바꾸지 않는다.**
비중만 줄이고, 어느 지수가 얼마여서 몇 배를 곱했는지를 근거표에 남긴다.

적재(jobs/signals)와 백테스트(jobs/backtest --trend-filter)가 같은 regime_from_closes 를 부른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from batch.core import db
from batch.core.turso import TursoClient
from batch.sources import nasdaq_symbols

# 지수 코드 → 야후 심볼. 한국거래소 지수 API 는 이용신청이 따로 필요해 보류 [확인필요]
INDEX_SYMBOLS: dict[str, str] = {"KOSPI": "^KS11", "KOSDAQ": "^KQ11", "SP500": "^GSPC"}
SYMBOL_INDEX: dict[str, str] = {v: k for k, v in INDEX_SYMBOLS.items()}

# 종목 시장 → 국면을 볼 지수. 미국은 **전 시장**에 S&P 500 (docs/signals.md 3.5 표).
# 2026-09-18 사용자 확정: NYSE·NASDAQ 은 상장 창구 차이일 뿐 시장 성격을 가르지 않아 지수를 나누지 않는다.
# 국내는 코스피·코스닥이 규모·성격이 다른 시장이라 나눈다.
#
# **미국 시장 이름을 손으로 적지 않는다** (2026-09-23, docs/infra.md 25.184).
# 예전에는 `NYSE`·`NASDAQ`·`AMEX` 셋이 적혀 있었다. `AMEX` 는 **아무도 안 만드는 죽은 키**
# 였고, 실제로 `stocks.market` 에 들어가는 `NYSE American`·`NYSE Arca`·`Cboe BZX`·`IEX`
# 는 하나도 없었다 — 그 종목들은 약세장에서도 **비중이 안 줄고** 근거표에 "국면을 몰라
# 줄이지 않았습니다" 라고 적혔다. 같은 실행에서 SP500 국면은 이미 계산돼 있는데도.
# 정의처는 `sources/nasdaq_symbols.US_MARKETS` 하나다.
MARKET_INDEX: dict[str, str] = {
    "KOSPI": "KOSPI",
    "KOSDAQ": "KOSDAQ",
    **{이름.upper(): "SP500" for 이름 in nasdaq_symbols.US_MARKETS},
}
COUNTRY_INDEXES: dict[str, tuple[str, ...]] = {"KR": ("KOSPI", "KOSDAQ"), "US": ("SP500",)}

# 원본의 10개월 ≈ 200 거래일. 원본도 200일 SMA 와 결과가 비슷하다고 적었다
SMA_DAYS = 200
# 지수 마지막 값이 이보다 오래됐으면 국면을 내지 않는다. 환율(services/fx)과 같은 여유
MAX_STALE_DAYS = 7

SETTING_KEY = "trend_filter"
DEFAULT_ENABLED = True  # web/lib/settings.ts DEFAULT_SETTINGS 와 같아야 한다
DEFAULT_BEAR_FACTOR = 0.5  # 추정 [확인필요: 백테스트 전후 비교로 조정]

STATE_LABEL = {"bull": "강세", "bear": "약세", "unknown": "미판정"}


@dataclass(frozen=True)
class Regime:
    index_code: str
    state: str  # bull · bear · unknown
    date: str | None = None  # 마지막 지수 날짜
    close: float | None = None
    sma: float | None = None
    days: int = 0  # 이동평균에 쓴 일수
    source: str | None = None
    note: str | None = None  # unknown 인 이유

    def as_dict(self) -> dict[str, Any]:
        return {
            "index_code": self.index_code,
            "state": self.state,
            "date": self.date,
            "close": self.close,
            "sma": self.sma,
            "days": self.days,
            "source": self.source,
            "note": self.note,
        }

    def describe(self) -> str:
        """리포트·근거표 한 조각. 'KOSPI 2,512 < 200일선 2,640 → 약세'"""
        if self.state == "unknown" or self.close is None or self.sma is None:
            return f"{self.index_code} 미판정 ({self.note or '지수 없음'})"
        sign = "<" if self.state == "bear" else "≥"
        # 반올림으로 두 값이 같아 보이면 자리를 늘린다 — "2,640 < 2,640" 은 모순이다 (25.489, 25.369 와 같은 모양).
        # 지수 날짜를 붙인다 — 최대 며칠 묵은 지수로 판정할 수 있다
        자리 = 가르는_자리(self.close, self.sma)
        날 = f" ({self.date})" if self.date else ""
        return (f"{self.index_code} {self.close:,.{자리}f} {sign} 200일선 {self.sma:,.{자리}f}"
                f" → {STATE_LABEL[self.state]}{날}")


def 가르는_자리(a: float, b: float) -> int:
    """두 값이 반올림으로 같아 보이지 않을 만큼의 소수 자리(최대 4). 머리 줄과 근거표 행이 **같이** 쓴다 (25.688)."""
    자리 = 0
    while 자리 < 4 and f"{a:,.{자리}f}" == f"{b:,.{자리}f}":
        자리 += 1
    return 자리


def index_for_market(market: str | None) -> str | None:
    return MARKET_INDEX.get((market or "").upper())


def regime_from_closes(
    index_code: str,
    closes: list[tuple[str, float]],
    as_of: str,
    source: str | None = None,
) -> Regime:
    """(날짜, 종가) 목록에서 as_of 기준 국면. as_of 뒤의 값은 보지 않는다 (백테스트 look-ahead 방지)."""
    known = sorted((d, c) for d, c in closes if d <= as_of)
    if not known:
        return Regime(index_code, "unknown", note="지수 없음", source=source)
    window = known[-SMA_DAYS:]
    last_date, last_close = window[-1]
    age = (date.fromisoformat(as_of) - date.fromisoformat(last_date)).days
    if age > MAX_STALE_DAYS:
        return Regime(
            index_code, "unknown", last_date, last_close, None, len(window), source,
            note=f"지수가 {age}일 오래됨",
        )
    if len(window) < SMA_DAYS:
        return Regime(
            index_code, "unknown", last_date, last_close, None, len(window), source,
            note=f"지수 {len(window)}일치뿐 ({SMA_DAYS}일 필요)",
        )
    sma = sum(c for _d, c in window) / len(window)
    state = "bear" if last_close < sma else "bull"
    return Regime(index_code, state, last_date, last_close, sma, len(window), source)


def factor_for(regime: Regime | None, settings: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """(비중 배수, 근거에 남길 것). 필터가 꺼져 있으면 (1.0, {})."""
    if not settings.get("enabled", DEFAULT_ENABLED):
        return 1.0, {}
    bear_factor = float(settings.get("bear_factor", DEFAULT_BEAR_FACTOR))
    if regime is None or regime.state == "unknown":
        data = {
            "regime": regime.as_dict() if regime else None,
            "regime_factor": 1.0,
            # 왜 모르는지(지수가 N일 오래됨·N일치뿐)를 함께 — 근거표가 이 문장을 먼저 쓴다 (docs/infra.md 25.688, 감사)
            "regime_note": "국면을 몰라 줄이지 않았습니다" + (f" ({regime.note})" if regime and regime.note else ""),
        }
        return 1.0, data
    factor = bear_factor if regime.state == "bear" else 1.0
    return factor, {"regime": regime.as_dict(), "regime_factor": factor, "bear_factor": bear_factor}


def load_settings(client: TursoClient) -> dict[str, Any]:
    """설정 trend_filter {enabled, bear_factor, warnings}. 없거나 깨졌으면 기본값.

    **범위 밖 배수는 기본값으로 되돌리고 말한다** (docs/infra.md 25.260). 예전에는 0~1 로 **조용히 잘랐다** —
    복구된 설정에 `bear_factor: 5` 가 있으면 1.0(= 약세장에도 안 줄임)이 되고 아무 말도 없었다. 다른 돈에 닿는 설정은
    모두 `settings_range.범위_안` 을 지나 기본값 + 경고로 간다(25.169~25.171). 이것만 그 문을 안 지났다.
    """
    from batch.core import settings_range as sr

    warnings: list[str] = []
    raw = db.get_setting(client, SETTING_KEY, None, 못읽음=warnings)
    enabled, factor = DEFAULT_ENABLED, DEFAULT_BEAR_FACTOR
    # **모양이 틀리면 말한다** (docs/infra.md 25.629, 감사). 예전에는 `enabled: "false"`(문자열)나 dict 가 아닌 값을
    # 말없이
    # 기본값(켜짐)으로 읽었다 — 설정 화면은 "읽지 못한 칸" 이라 알리는데 리포트는 아무 말 없이 약세 축소를 켰다
    if raw is not None and not isinstance(raw, dict):
        warnings.append(
            f"설정 trend_filter 를 읽지 못해(모양이 {type(raw).__name__})"
            f" 기본값(켬, ×{DEFAULT_BEAR_FACTOR})으로 계산했습니다"
        )
    if isinstance(raw, dict):
        if isinstance(raw.get("enabled"), bool):
            enabled = raw["enabled"]
        elif "enabled" in raw:
            warnings.append(
                f"설정 trend_filter.enabled 가 참/거짓이 아니라({raw['enabled']!r}) 기본값(켬)으로 계산했습니다"
            )
        값, 말 = sr.범위_안("bear_factor", raw.get("bear_factor"), DEFAULT_BEAR_FACTOR)
        factor = DEFAULT_BEAR_FACTOR if 값 is None else float(값)
        if 말:
            warnings.append(말)
    return {"enabled": enabled, "bear_factor": factor, "warnings": warnings}


def load_index_closes(
    client: TursoClient, index_code: str, as_of: str, days: int = SMA_DAYS + MAX_STALE_DAYS
) -> tuple[list[tuple[str, float]], str | None]:
    """as_of 이하 최근 days 개의 (날짜, 종가)와 마지막 행의 출처."""
    rs = client.execute(
        "SELECT date, close, source FROM index_prices WHERE index_code = ? AND date <= ?"
        " ORDER BY date DESC LIMIT ?",
        [index_code, as_of, days],
    )
    rows = [(str(r[0]), float(r[1]), str(r[2])) for r in rs.rows]
    closes = sorted((d, c) for d, c, _s in rows)
    return closes, (rows[0][2] if rows else None)


def regimes_for_country(client: TursoClient, country: str, as_of: str) -> dict[str, Regime]:
    """그 나라 종목이 쓰는 지수들의 국면. {index_code: Regime}"""
    out: dict[str, Regime] = {}
    for code in COUNTRY_INDEXES.get(country, ()):
        closes, source = load_index_closes(client, code, as_of)
        out[code] = regime_from_closes(code, closes, as_of, source)
    return out


def report_line(regimes: dict[str, Regime], settings: dict[str, Any]) -> str:
    """리포트 1부 머리의 한 줄. 무엇을 보고 얼마나 줄였는지가 먼저 보여야 한다."""
    if not settings.get("enabled", DEFAULT_ENABLED):
        return "시장 국면: 추세 필터 꺼짐 (설정)"
    parts = [r.describe() for r in regimes.values()]
    if not parts:
        return "시장 국면: 지수 없음"
    line = "시장 국면: " + " · ".join(parts)
    if any(r.state == "bear" for r in regimes.values()):
        line += f" — 약세 지수의 신규 매수 비중 ×{settings.get('bear_factor', DEFAULT_BEAR_FACTOR):.2f}"
    return line
