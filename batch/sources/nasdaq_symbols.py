"""미국 상장 종목 마스터.

나스닥이 공개하는 심볼 디렉터리를 쓴다. 업계가 그대로 쓰는 파일이고
인증이 필요 없다. 2026-09-16 에 실제로 받아 구조를 확인했다.

  https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt   나스닥 상장
  https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt    그 외 거래소

왜 지수 구성종목 목록 대신 이것을 쓰는가
  CLAUDE.md 의 유니버스 규칙은 지수 편입 여부가 아니라 시총과 거래대금이다.
  지수 목록은 출처마다 라이선스가 걸리고 갱신이 늦지만, 이 파일은 거래소가
  매일 갱신하고 전체 종목을 준다. 규칙에 더 맞는다.

**함정: 두 파일의 열 순서가 다르다.** 같은 뜻의 열이 다른 위치에 있어
하나로 뭉뚱그려 파싱하면 ETF 를 보통주로, 보통주를 테스트 종목으로 읽는다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import requests

from batch import config
from batch.sources.yfinance_src import FetchResult

log = logging.getLogger(__name__)

SOURCE = "nasdaqtrader_symdir"
BASE_URL = "https://www.nasdaqtrader.com/dynamic/SymDir"
TIMEOUT = 60

# HTTP 헤더는 latin-1 로만 인코딩된다. 한글을 넣으면 요청 자체가 실패한다.
USER_AGENT = "stock-manager-personal/0.1 (personal non-commercial use)"

# 파일마다 열 이름이 다르다. 위치가 아니라 이름으로 찾는다.
FILES = {
    "nasdaqlisted.txt": {
        "symbol": "Symbol",
        "name": "Security Name",
        "etf": "ETF",
        "test": "Test Issue",
        "exchange": None,  # 이 파일은 전부 나스닥이다
        "default_exchange": "NASDAQ",
    },
    "otherlisted.txt": {
        "symbol": "ACT Symbol",
        "name": "Security Name",
        "etf": "ETF",
        "test": "Test Issue",
        "exchange": "Exchange",
        "default_exchange": "",
    },
}

# otherlisted.txt 의 Exchange 코드
#: **`stocks.market` 에 들어가는 미국 시장 이름의 정의처다** (docs/infra.md 25.184).
#: `jobs/universe` 가 `s.exchange` 를 그대로 `stocks.market` 에 넣는다. 그래서 이 표가
#: 바뀌면 시장 이름으로 판단하는 모든 곳(`services/trend.MARKET_INDEX`·웹 스크리너)이
#: 따라와야 한다 — `tests/test_market_names.py` 가 양방향으로 대 본다
EXCHANGE_CODES = {
    "A": "NYSE American",
    "N": "NYSE",
    "P": "NYSE Arca",
    "Z": "Cboe BZX",
    "V": "IEX",
}

#: `stocks.market` 에 들어갈 수 있는 미국 시장 이름 **전부**.
#: `nasdaqlisted.txt` 는 전부 나스닥이고(`default_exchange`), 나머지는 위 표에서 온다.
US_MARKETS: tuple[str, ...] = (
    FILES["nasdaqlisted.txt"]["default_exchange"],
    *sorted(EXCHANGE_CODES.values()),
)

# 이름에 이런 말이 들어가면 보통주가 아니다.
# 우선주, 신주인수권, 유닛, 예탁증서는 재무 비교의 잣대가 다르다.
NON_COMMON_MARKERS = (
    "Preferred",
    "Warrant",
    " Right",
    "Rights",
    " Unit",
    "Units",
    "Depositary",
    "Depository",
    "Notes",
    "Debenture",
    "% Note",
    "Trust Preferred",
)

#: **낱말로 찾는다** (docs/infra.md 25.528·25.536, 감사·교차검증). 글자 조각으로 찾아 " Unit" 이 "First **Unit**ed",
#: "Community **Unit**y" 에 걸려 멀쩡한 보통주가 사유 기록도 없이 빠졌다. 그런데 뒤 경계만 두면
#: 복수형("Warrants"·"Debentures"·"Preferreds")을 놓쳐 워런트가 보통주로 들어왔다
#: (나스닥 워런트는 심볼 접미사 그물도 없다). 그래서
#:  - 뒤: 표지 뒤에 "s" 가 붙어도 되고, 그 뒤는 낱말 끝
#:  - 앞: 옛 표지가 공백으로 시작했으면(" Unit") 앞에 공백·하이픈·괄호가 있어야 한다 — 이름이 "Unit Corporation" 으로
#:    시작하는 종목은 옛 판정대로 통과. 공백 없는 표지는 옛 판정처럼 어디서나(낱말 앞 경계만)
#:  - "%" 로 시작하는 표지("% Note")는 옛 판정 그대로 글자로 찾는다
def _표지_식(m: str) -> str:
    본 = m.strip()
    if 본.startswith("%"):
        return re.escape(본)
    앞 = r"(?<=[\s\-(])" if m.startswith(" ") else r"\b"
    return 앞 + re.escape(본) + r"s?\b"


_NON_COMMON_RE = re.compile("|".join(_표지_식(m) for m in NON_COMMON_MARKERS))

# ----------------------------------------------------------------------
# 심볼 형태로 거르는 규칙 (2026-09-16 추가)
# ----------------------------------------------------------------------
#
# 이름만으로 거르다가 43종목을 놓쳤다. 미국 전종목 수집을 처음 운영에서
# 돌렸을 때(실행 35083646396) 야후가 이렇게 답한 종목들이다.
#
#   ['TRTN$C', 'TRTN$D', 'TRTN$E', ...]: No data found, symbol may be delisted
#   ['AAC-W', 'BIII-W', 'BEBE-W', ...]:  No data found, symbol may be delisted
#
# 전부 우선주($)와 워런트(-W)였다. **야후에 존재하지 않는 종목을 매 거래일
# 다시 물어보는 것**이라 시간과 호출을 버린다. 이름 판정이 왜 이들을
# 놓쳤는지는 확인하지 못했다 [확인필요]. 다만 심볼 형태는 실패 목록이
# 그대로 증거이므로, 이름과 별개로 형태로도 거른다.
#
# **주의: 클래스 주식을 함께 버리면 안 된다.** BRK.B 와 BF.B 는 어엿한
# 보통주다. 그래서 접미 한 글자를 통째로 막지 않고, 증권 종류를 뜻하는
# 코드만 골라서 막는다. B 나 A 는 여기 없다.

# 접미 코드가 뜻하는 것: W 워런트, WS·WT 워런트, U 유닛, R·RT 신주인수권
NON_COMMON_SUFFIXES = frozenset({"W", "WS", "WT", "U", "R", "RT"})

# 우선주를 나타내는 구분자. 나스닥 표기에서 이 문자는 우선주에만 쓰인다.
PREFERRED_MARKER = "$"

# 심볼과 접미 코드를 가르는 구분자.
# 거래소 파일이 점을 쓰기도 하고 하이픈을 쓰기도 해서 둘 다 본다.
SYMBOL_SEPARATORS = (".", "-", "$")


def _suffix_of(symbol: str) -> str:
    """마지막 구분자 뒤의 조각. 구분자가 없으면 빈 문자열."""
    position = max(symbol.rfind(sep) for sep in SYMBOL_SEPARATORS)
    if position < 0:
        return ""
    return symbol[position + 1 :].upper()


@dataclass
class UsSymbol:
    symbol: str
    name: str
    exchange: str
    is_etf: bool
    is_test: bool

    @property
    def is_common_stock(self) -> bool:
        """보통주로 볼 수 있는가.

        세 겹으로 거른다. ETF·테스트 종목, 이름, 그리고 심볼 형태다.
        거래소가 증권 종류 열을 주지 않아 이름과 심볼로 판정할 수밖에 없다.

        **심볼 형태 판정은 나중에 붙였다.** 이름만으로 거르다가 43종목을
        놓쳤고, 그 종목들을 매 거래일 야후에 헛되이 물어보고 있었다.
        자세한 경위는 NON_COMMON_SUFFIXES 위의 주석에 적었다.
        """
        if self.is_etf or self.is_test:
            return False
        if _NON_COMMON_RE.search(self.name):
            return False
        return not self.looks_like_non_common

    @property
    def looks_like_non_common(self) -> bool:
        """심볼 생김새가 보통주가 아닌가.

        BRK.B 같은 클래스 주식은 살려야 하므로 접미 한 글자를 통째로 막지
        않는다. 증권 종류를 뜻하는 코드만 본다.
        """
        if PREFERRED_MARKER in self.symbol:
            return True
        return _suffix_of(self.symbol) in NON_COMMON_SUFFIXES

    @property
    def yahoo_symbol(self) -> str:
        """야후 파이낸스가 쓰는 표기.

        거래소는 클래스 구분에 점을 쓰고(BRK.B) 야후는 하이픈을 쓴다(BRK-B).
        이걸 맞추지 않으면 그 종목만 조용히 빠진다.
        """
        return self.symbol.replace(".", "-")


def _parse(text: str, spec: dict[str, str | None]) -> list[UsSymbol]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    header = lines[0].split("|")
    index = {name: i for i, name in enumerate(header)}

    for key in ("symbol", "name", "etf", "test"):
        column = spec[key]
        if column not in index:
            raise ValueError(f"기대한 열이 없습니다: {column}. 파일 형식이 바뀌었습니다")
    # **끝 표지가 없으면 잘린 파일이다** (docs/infra.md 25.527, 교차검증). 목록에서 사라진 종목을 상장폐지로 적으므로
    # (25.519) 잘린 파일을 받으면 알파벳 뒤쪽 멀쩡한 종목이 한 주 동안 폐지가 됐다. 80% 비교는 받은 쪽에 ETF·우선주가
    # 섞여 부풀어 있어 약했다
    if len(lines) > 1 and not lines[-1].startswith("File Creation Time"):
        raise ValueError("파일 끝 표지(File Creation Time)가 없습니다 — 응답이 잘렸습니다")

    rows: list[UsSymbol] = []
    for line in lines[1:]:
        # 파일 끝에 "File Creation Time: ..." 줄이 붙는다. 종목이 아니다.
        if line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) < len(header):
            continue

        exchange_col = spec["exchange"]
        exchange_raw = parts[index[exchange_col]] if exchange_col else ""
        exchange = EXCHANGE_CODES.get(
            exchange_raw.strip(), exchange_raw.strip()
        ) or str(spec["default_exchange"])

        rows.append(
            UsSymbol(
                symbol=parts[index[str(spec["symbol"])]].strip(),
                name=parts[index[str(spec["name"])]].strip(),
                exchange=exchange,
                is_etf=parts[index[str(spec["etf"])]].strip().upper() == "Y",
                is_test=parts[index[str(spec["test"])]].strip().upper() == "Y",
            )
        )
    return rows


def fetch_symbols() -> FetchResult:
    """두 파일을 받아 합친다. 중복 심볼은 먼저 온 쪽을 남긴다."""
    if config.SETTINGS.offline_mode:
        return _offline()

    all_rows: list[UsSymbol] = []
    seen: set[str] = set()

    for filename, spec in FILES.items():
        try:
            response = requests.get(
                f"{BASE_URL}/{filename}",
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            return FetchResult(ok=False, source=SOURCE, error=f"{filename} 받기 실패: {exc}")

        if response.status_code != 200:
            return FetchResult(
                ok=False, source=SOURCE, error=f"{filename} HTTP {response.status_code}"
            )

        try:
            rows = _parse(response.text, spec)
        except ValueError as exc:
            return FetchResult(ok=False, source=SOURCE, error=str(exc))

        for row in rows:
            if row.symbol and row.symbol not in seen:
                seen.add(row.symbol)
                all_rows.append(row)

    if not all_rows:
        return FetchResult(ok=False, source=SOURCE, error="받은 종목이 없습니다")

    return FetchResult(
        ok=True,
        source=SOURCE,
        data=all_rows,
        limit_state="ok",
        fetched_at=datetime.now(UTC),
    )


def _offline() -> FetchResult:
    import json
    from pathlib import Path

    fixture = Path(config.ROOT) / "tests" / "fixtures" / "us_symbols.json"
    if not fixture.exists():
        return FetchResult(ok=False, source=SOURCE, error=f"픽스처가 없습니다: {fixture}")

    raw = json.loads(fixture.read_text(encoding="utf-8"))
    rows = [UsSymbol(**row) for row in raw.get("symbols", [])]
    return FetchResult(ok=True, source=SOURCE, data=rows, from_cache=True, limit_state="ok")
