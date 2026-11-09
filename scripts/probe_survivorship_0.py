"""42회차 u3 생존편향 ⓪ 자료 탐침 — 한국거래소·DART 를 **읽기만** 불러 자료 꼴을 본다.

docs/factors.md 12.45 "u3 생존편향 사전 등록" 의 ⓪ 을 그대로 옮겼다.

⓪ 은 "지금 허용되는 유일한 걸음" 이다. gap 은 내지 않는다 — 값이 아니라 자료 꼴(셈·있음 표시·비율·응답 시간)만 찍는다.
종목코드·종목명·가격·금액은 찍지 않는다. 출력은 워크플로가 `scripts/ops_tee.py` 로 받아 비공개 운영 이슈로만 보낸다
(공개 로그엔 줄 수만). **판정 실행 경로가 이 모듈을 부르지 않는다**
(12.45 "탐침 모듈을 전략·점수 경로가 import 하지 않는다" — `tests/test_probe_survivorship_0_2198.py` 가 묶는다).

⓪ 이 확인할 것 (12.45 그대로)
  A. 과거 `basDd` 일별매매정보(코스피 `stk_bydd_trd`·코스닥 `ksq_bydd_trd`)에 그 뒤 사라진 종목 행이 드는가
  B. 정지 중 행 모양(행 없음·거래대금 0)
  C. 정리매매 기간 행이 드는가(|FLUC_RT| > 30% 날)
  D. MKTCAP ≈ 종가 × LIST_SHRS(같은 날), LIST_SHRS 가 기준일마다 바뀌는가 — `scripts/probe_krx_listed_shares.py`
     (한 번도 돌지 않음)의 두 질문을 같은 허용 오차(0.1%)로 이 탐침에 묶었다. 그 스크립트는 손으로 고른 종목을 받지만
     여기서는 규칙으로 고른다(보유가 드러나지 않게)
  E. 원 응답에 우선주·관리종목·소속부(SECT_TP_NM) 칸이 있는가,
     우선주 규칙 둘(단축코드 끝자리 ≠ 0 · 이름 "우"·"우B")의 어긋남 수
  F. FLUC_RT 기준가가 권리락·액면분할 기준가 대비인가(분할로 보이는 종목 몇)
  G. DART 로 폐지 회사의 상장일·폐지일·폐지 사유를 낼 수 있는가 `[확인필요: 출처]` — 모르면 "모름" 으로 찍는다
  H. 호출 1회 응답 시간(① 의 25~90분 어림 확인)

표본을 고르는 규칙 (결정적 — 사람이 종목·날짜를 고르지 않는다)
  - 기준일: 2021-09 부터 해마다 3월·9월 마지막 국내 거래일 + 오늘 앞 마지막 국내 거래일(닻). 시장마다 1회씩
  - 사라진 종목: 지난 표본 어딘가에 있었고 닻 응답(두 시장 모두)에 없는 단축코드.
    닻에서 다른 시장에 있으면 이전상장으로 따로 센다
  - 정리매매 창: 시장마다 사라진 종목 하나 — 마지막으로 보인 표본이 가장 늦은 것, 같으면 단축코드가 가장 작은 것.
    그 표본과 다음 표본 사이를 거래일 이분 탐색으로 좁혀 마지막 관측일을 찾고,
    그 앞 `CLEANUP_WINDOW` 거래일을 하루씩 본다
  - 분할: 잇단 두 표본 사이 LIST_SHRS 가 정수배(≥2, 1% 안)로 늘고 종가가 대략 그만큼 준 종목 — 나중 표본이 늦은 것부터,
    같으면 단축코드 작은 것부터 `SPLIT_SAMPLES` 개. 두 표본 사이를 이분 탐색으로 좁혀 새 주식수 첫날을 찾는다
  - DART: 사라진 종목 가운데 고유번호 목록(corpCode.xml)의 종목코드와 맞는 것 —
    마지막 관측이 늦은 것부터 `DART_CORP_SAMPLES` 개

호출 상한 KRX ≤ 100 · DART ≤ 100 (재시도까지 센다). 카운터는 시작 때 `usage_blocked_today`, 끝에 `record_and_guard`
로 몰아 적는다(80% 경고·100% 멈춤). Turso 는 `api_usage` 카운터 읽기·쓰기만 — 시세·`stocks`·`batch_runs` 쓰기 0.
일일 배치 시각·판정 실행 창(그 달 3~7일)에는 부르지 않는다.

    python scripts/probe_survivorship_0.py 2>&1 | python scripts/ops_tee.py survivorship-0.txt
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch import config  # noqa: E402
from batch.core import calendar as cal  # noqa: E402
from batch.core import db  # noqa: E402
from batch.sources import dart, krx  # noqa: E402
from batch.sources.krx import KrxDailyRow, KrxMasterRow  # noqa: E402

#: 12.45 "⓪ KRX·DART 각 ≤ 100회" — 재시도까지 센다
KRX_MAX_CALLS = 100
DART_MAX_CALLS = 100
#: 카운터 이름 — `db.API_NAMES` 의 것을 그대로 (세는 자리가 둘이면 판정이 틀린다, 25.68)
KRX_COUNTER = krx.SOURCE
DART_COUNTER = dart.SOURCE
#: ① 호출 구간 첫날(12.45 — 2021-09 전은 금고 B 라 부르지 않는다)
HISTORY_START = date(2021, 9, 1)
#: MKTCAP ≈ 종가 × LIST_SHRS 허용 오차 — `probe_krx_listed_shares.py` 의 `0.001 * row.market_cap` 과 같다(시험이 묶는다)
MKTCAP_TOL = 0.001
#: 정리매매로 읽는 하루 등락 문턱(12.45 "|FLUC_RT| > 30% 날")
BIG_MOVE_PCT = 30.0
#: FLUC_RT 와 원 종가 비율이 갈렸다고 보는 폭(12.45 ① "1%p")
FLUC_GAP_PP = 1.0
#: 마지막 관측일 앞으로 하루씩 볼 거래일 수 — 정리매매는 보통 7거래일 `[확인필요: 거래소 규정]` 이라 조금 넉넉히
CLEANUP_WINDOW = 10
#: 분할 표본 수 · DART 회사 표본 수
SPLIT_SAMPLES = 2
DART_CORP_SAMPLES = 5
#: DART 거래소공시 창 — 마지막 관측 앞 1년 ~ 뒤 60일
DART_LOOKBACK_DAYS = 365
DART_LOOKAHEAD_DAYS = 60
#: KRX 호출 사이 쉼(초) — ① 명세의 0.5초 간격과 같게
PAUSE_SECONDS = 0.5
#: ① 의 KRX 호출 어림(12.45 "약 2,600회 [어림]") — 응답 시간으로 걸릴 시간을 다시 어림한다
PHASE1_CALLS = 2600
#: 국내 일일 배치를 피하는 시각(KST) — 예약 08:27, 한국거래소 전일 시세 공개 08:00 전후(CLAUDE.md)
KR_BATCH_AVOID = (dtime(7, 30), dtime(10, 0))
#: 미국 일일 배치(정규장 1시간 전)를 피하는 폭 — 정규장 시작 앞 2시간 ~ 시작
US_BATCH_AVOID = timedelta(hours=2)
#: 판정 실행 창(그 달 3~7일, `batch/services/scoreboard.py` 코호트 1)
JUDGE_DAYS = range(3, 8)

KST = ZoneInfo("Asia/Seoul")
MARKETS = ("KOSPI", "KOSDAQ")
DAILY_API = {"KOSPI": "kospi_daily", "KOSDAQ": "kosdaq_daily"}
MASTER_API = {"KOSPI": "kospi_master", "KOSDAQ": "kosdaq_master"}
#: `KrxDailyRow.from_raw` 가 읽는 원 응답 키 — 이 밖의 키가 오면 "파싱 행에 없는 칸" 으로 이름만 찍는다
PARSED_DAILY_KEYS = frozenset({
    "BAS_DD", "ISU_CD", "ISU_NM", "MKT_NM", "TDD_CLSPRC", "CMPPREVDD_PRC", "FLUC_RT", "TDD_OPNPRC", "TDD_HGPRC",
    "TDD_LWPRC", "ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP", "LIST_SHRS",
})  # fmt: skip
#: 소속부·관리·우선주로 읽힐 수 있는 원 응답 키 모양 — 이름만 맞춰 본다(값 뜻은 `[확인필요]`)
SECTION_KEY_RE = re.compile(r"SECT|ADMIN|KIND_STKCERT|STKCERT|WARN|ISSUE", re.IGNORECASE)
#: DART 응답에서 상장일·폐지일로 읽힐 수 있는 키 모양. 맞는 키가 없으면 그 항목은 "모름" — 키 이름을 지어내지 않는다
LISTING_KEY_RE = re.compile(r"(^|_)(lst|list|listing|dlst|delist)(_|$)|상장|폐지", re.IGNORECASE)
DELIST_TITLE = "상장폐지"
DART_URL = dart.BASE_URL


# ----------------------------------------------------------------------------------------------------------------------
# 호출 상한
# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class Budget:
    """한 출처의 호출 상한. **재시도까지 센다** — 한 번 부를 때 최악 `per_call` 번 나갈 수 있어
    남은 몫이 그만큼 있어야 부른다."""

    name: str
    cap: int
    per_call: int = 1
    sent: int = 0  # 실제로 나간 요청 수(재시도 포함) — 카운터에 이 수를 적는다
    refused: int = 0
    seconds: list[float] = field(default_factory=list)
    stopped: str = ""  # 한도·오류로 멈췄으면 그 까닭

    def can_call(self) -> bool:
        if self.stopped or self.sent + self.per_call > self.cap:
            self.refused += 1
            return False
        return True

    def spend(self, attempts: int, seconds: float) -> None:
        self.sent += max(0, min(attempts, self.per_call))
        self.seconds.append(seconds)


# ----------------------------------------------------------------------------------------------------------------------
# KRX 읽기
# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class Snapshot:
    market: str
    day: date
    rows: dict[str, KrxDailyRow]
    keys: frozenset[str]


class KrxReader:
    """`krx.fetch`(원 응답) + `KrxDailyRow.from_raw`(파싱) = `krx.fetch_daily` 와 같은 길.
    원 응답 키를 보려고 둘로 나눠 부른다."""

    def __init__(
        self,
        budget: Budget,
        fetch: Callable[[str, str], Any] = krx.fetch,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.budget, self._fetch, self._clock, self._sleep = budget, fetch, clock, sleep
        self.cache: dict[tuple[str, date], Snapshot | None] = {}
        self.failures = 0
        self.empty = 0

    def _call(self, api_name: str, day: date) -> tuple[bool, Any | None]:
        """(불렀나, 성공한 결과). 상한·멈춤이면 (False, None) — 부르지 않았다."""
        if not self.budget.can_call():
            return False, None
        if self.budget.sent:
            self._sleep(PAUSE_SECONDS)
        t0 = self._clock()
        result = self._fetch(api_name, day.strftime("%Y%m%d"))
        self.budget.spend(int(getattr(result, "attempts", 1) or 0), self._clock() - t0)
        if not result.ok:
            self.failures += 1
            if getattr(result, "limit_state", "") == "blocked":
                self.budget.stopped = "거절(blocked)"
            return True, None
        return True, result

    def day(self, market: str, day: date) -> Snapshot | None:
        key = (market, day)
        if key in self.cache:
            return self.cache[key]
        불렀나, result = self._call(DAILY_API[market], day)
        if result is None:
            if 불렀나:
                self.cache[key] = None  # 실패한 날은 다시 부르지 않는다
            return None
        raw = list(result.data or [])
        if not raw:
            self.empty += 1
        keys = frozenset(k for r in raw for k in r)
        rows = {}
        for r in raw:
            row = KrxDailyRow.from_raw(r)
            if row.isu_cd:
                rows[row.isu_cd] = row
        snap = Snapshot(market, day, rows, keys)
        self.cache[key] = snap
        return snap

    def master(self, market: str, day: date) -> tuple[frozenset[str], list[KrxMasterRow]] | None:
        _, result = self._call(MASTER_API[market], day)
        if result is None:
            return None
        raw = list(result.data or [])
        return frozenset(k for r in raw for k in r), [KrxMasterRow.from_raw(r) for r in raw]


# ----------------------------------------------------------------------------------------------------------------------
# 표본 규칙
# ----------------------------------------------------------------------------------------------------------------------
def kr_sessions(start: date, end: date) -> list[date]:
    import pandas as pd

    c = cal.exchange_calendar("KR")
    return [ts.date() for ts in c.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))]


def sample_dates(anchor: date, sessions: Callable[[date, date], list[date]] = kr_sessions) -> list[date]:
    """2021-09 부터 해마다 3월·9월 마지막 거래일(닻보다 앞) + 닻. 오름차순, 겹침 없음."""
    out: list[date] = []
    y, m = HISTORY_START.year, HISTORY_START.month
    while (y, m) <= (anchor.year, anchor.month):
        if m in (3, 9):
            month_end = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
            days = sessions(date(y, m, 1), month_end)
            if days and days[-1] < anchor:
                out.append(days[-1])
        m += 1
        if m == 13:
            y, m = y + 1, 1
    out.append(anchor)
    return sorted(set(out))


def bisect_edge(days: list[date], pred: Callable[[date], bool | None]) -> int | None:
    """`pred(days[0])` 참·`pred(days[-1])` 거짓을 안다고 보고, 참인 마지막 자리를 찾는다. 모르면(None) 멈춘다."""
    lo, hi = 0, len(days) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        v = pred(days[mid])
        if v is None:
            return None
        lo, hi = (mid, hi) if v else (lo, mid)
    return lo


# ----------------------------------------------------------------------------------------------------------------------
# 판정 (순수 함수 — 시험이 손 계산으로 본다)
# ----------------------------------------------------------------------------------------------------------------------
def preferred_by_code(code: str) -> bool:
    """단축코드 끝자리 ≠ 0 (12.45 규칙 하나)."""
    return len(code) == 6 and code[-1] != "0"


def preferred_by_name(name: str) -> bool:
    """이름이 "우"·"우B" 로 끝난다 (12.45 규칙 둘)."""
    return re.search(r"우B?$", name.strip()) is not None


def mktcap_match(row: KrxDailyRow) -> bool | None:
    """같은 날 MKTCAP ≈ 종가 × LIST_SHRS (0.1% 안). 칸이 비면 모름."""
    if not (row.close and row.listed_shares and row.market_cap):
        return None
    return abs(row.close * row.listed_shares - row.market_cap) <= MKTCAP_TOL * row.market_cap


def halted_shape(rows: Iterable[KrxDailyRow]) -> Counter:
    """거래대금 0·거래량 0·종가 빔/0·(거래대금 0 가운데) 시가 0 의 셈."""
    c: Counter = Counter()
    for r in rows:
        c["행"] += 1
        if not r.value:
            c["거래대금0"] += 1
            if not r.open:
                c["거래대금0_시가0"] += 1
            if r.close:
                c["거래대금0_종가있음"] += 1
        if not r.volume:
            c["거래량0"] += 1
        if not r.close:
            c["종가없음"] += 1
    return c


def presence(snaps: dict[str, list[Snapshot]], anchor: dict[str, Snapshot]) -> tuple[dict, dict]:
    """(셈, 사라진 종목 {(시장, 코드): 마지막으로 보인 표본일}).

    이전상장(닻에서 다른 시장)은 사라진 것으로 세지 않는다."""
    닻전체 = set().union(*(s.rows for s in anchor.values()))
    셈: dict[str, Any] = {}
    사라짐: dict[tuple[str, str], date] = {}
    for m in MARKETS:
        if m not in anchor:
            continue
        본: dict[str, date] = {}
        for s in snaps.get(m, []):
            if s.day >= anchor[m].day:
                continue
            for code in s.rows:
                본[code] = max(본.get(code, s.day), s.day)
        다른 = set().union(*(anchor[o].rows for o in MARKETS if o != m and o in anchor))
        이전 = [c for c in 본 if c not in anchor[m].rows and c in 다른]
        없음 = {c: d for c, d in 본.items() if c not in 닻전체}
        셈[m] = {"지난표본종목": len(본), "사라짐": len(없음), "이전상장": len(이전)}
        사라짐.update({(m, c): d for c, d in 없음.items()})
    return 셈, 사라짐


def list_shares_changes(snaps: list[Snapshot]) -> tuple[int, int]:
    """(두 표본 이상 보인 종목 수, LIST_SHRS 가 한 번이라도 바뀐 종목 수)."""
    값: dict[str, set[int]] = {}
    for s in snaps:
        for code, r in s.rows.items():
            if r.listed_shares:
                값.setdefault(code, set()).add(r.listed_shares)
    여럿 = [c for c in 값 if sum(1 for s in snaps if c in s.rows and s.rows[c].listed_shares) >= 2]
    return len(여럿), sum(1 for c in 여럿 if len(값[c]) > 1)


def split_ratio(old: KrxDailyRow, new: KrxDailyRow) -> int | None:
    """두 표본 사이 주식수가 정수배(≥2, 1% 안)로 늘고 종가가 대략 그만큼(1.5배 안) 줄었으면 그 배수."""
    if not (old.listed_shares and new.listed_shares and old.close and new.close):
        return None
    r = new.listed_shares / old.listed_shares
    k = round(r)
    if k < 2 or abs(r - k) / k > 0.01:
        return None
    px = old.close / new.close
    return k if k / 1.5 <= px <= k * 1.5 else None


def split_events(snaps: list[Snapshot]) -> list[tuple[date, str, date, int]]:
    """(나중 표본일, 코드, 앞 표본일, 배수) — 나중 표본이 늦은 것부터, 같으면 코드 작은 것부터."""
    out = []
    for a, b in zip(snaps, snaps[1:], strict=False):
        for code, new in b.rows.items():
            old = a.rows.get(code)
            k = split_ratio(old, new) if old else None
            if k:
                out.append((b.day, code, a.day, k))
    return sorted(out, key=lambda e: (-e[0].toordinal(), e[1]))


def classify_base(prev: KrxDailyRow | None, first: KrxDailyRow | None, k: int) -> str:
    """새 주식수 첫날의 전일 대비 기준가(종가 − CMPPREVDD_PRC)가 권리락 기준가(전날 종가 ÷ 배수)인가, 원 종가인가."""
    if first is None or first.close is None or first.change is None or prev is None or not prev.close:
        return "모름"
    기준 = first.close - first.change
    권리락 = prev.close / k
    if abs(기준 - 권리락) <= 0.01 * 권리락:
        return "권리락 기준가"
    if abs(기준 - prev.close) <= 0.01 * prev.close:
        return "원 종가"
    return "그 밖"


def cleanup_shape(window: list[KrxDailyRow | None]) -> Counter:
    """마지막 관측일까지 하루씩 — 행 있음·|FLUC_RT|>30%·거래대금 0·FLUC_RT 와 원 종가 비율이 1%p 넘게 갈린 날."""
    c: Counter = Counter()
    앞: KrxDailyRow | None = None
    for r in window:
        c["거래일"] += 1
        if r is None:
            c["행없음"] += 1
            continue
        c["행있음"] += 1
        if r.change_pct is not None and abs(r.change_pct) > BIG_MOVE_PCT:
            c["큰등락"] += 1
        if not r.value:
            c["거래대금0"] += 1
        if 앞 is not None and 앞.close and r.close and r.change_pct is not None:
            원 = (r.close / 앞.close - 1) * 100
            if abs(원 - r.change_pct) > FLUC_GAP_PP:
                c["FLUC_원종가_갈림"] += 1
        앞 = r
    return c


def row_gaps(days: list[Snapshot]) -> int:
    """잇단 거래일 응답에서 앞날·뒷날엔 있고 가운데 날엔 없는 (날, 종목) 수 — 정지 중 '행 없음' 꼴."""
    n = 0
    for a, b, c in zip(days, days[1:], days[2:], strict=False):
        n += len((set(a.rows) & set(c.rows)) - set(b.rows))
    return n


def pct(a: int, b: int) -> str:
    return f"{a / b:.1%}" if b else "모름"


# ----------------------------------------------------------------------------------------------------------------------
# DART 읽기
# ----------------------------------------------------------------------------------------------------------------------
class DartReader:
    def __init__(self, budget: Budget, key: str, http_get: Callable[..., Any] | None = None,
                 corp_codes: Callable[[], Any] = dart.fetch_corp_codes,
                 clock: Callable[[], float] = time.monotonic) -> None:  # fmt: skip
        import requests

        self.budget, self.key, self._clock = budget, key, clock
        self._get = http_get or requests.get
        self._corp_codes = corp_codes
        self.status: Counter = Counter()

    def corp_codes(self) -> list[dart.CorpCode] | None:
        if not self.budget.can_call():
            return None
        t0 = self._clock()
        r = self._corp_codes()
        self.budget.spend(1, self._clock() - t0)
        if not r.ok:
            self.status["corpCode 실패"] += 1
            return None
        return list(r.data or [])

    def _json(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if not self.budget.can_call():
            return None
        t0 = self._clock()
        try:
            body = self._get(f"{DART_URL}/{endpoint}", params={"crtfc_key": self.key, **params}, timeout=60).json()
        except Exception as exc:  # noqa: BLE001 — 예외 문구의 URL 에 인증키가 있다(25.621) — 종류만
            self.budget.spend(1, self._clock() - t0)
            self.status[f"{endpoint} 예외 {type(exc).__name__}"] += 1
            return None
        self.budget.spend(1, self._clock() - t0)
        st = str(body.get("status", ""))
        self.status[f"{endpoint} {st}"] += 1
        if st == dart.STATUS_DAILY_LIMIT:
            self.budget.stopped = "DART 020"
        return body

    def company(self, corp_code: str) -> dict[str, Any] | None:
        return self._json("company.json", {"corp_code": corp_code})

    def exchange_disclosures(self, corp_code: str, bgn: date, end: date) -> dict[str, Any] | None:
        return self._json("list.json", {"corp_code": corp_code, "bgn_de": bgn.strftime("%Y%m%d"),
                                        "end_de": end.strftime("%Y%m%d"), "pblntf_ty": "I",
                                        "page_no": 1, "page_count": 100})  # fmt: skip


# ----------------------------------------------------------------------------------------------------------------------
# 시각·카운터 문
# ----------------------------------------------------------------------------------------------------------------------
def window_refusal(now: datetime, us_open: Callable[[date], datetime | None] | None = None) -> str | None:
    """판정 실행 창(그 달 3~7일 — UTC·KST 어느 쪽이든)·국내/미국 일일 배치 시각이면 그 까닭."""
    kst = now.astimezone(KST)
    if now.astimezone(UTC).day in JUDGE_DAYS or kst.day in JUDGE_DAYS:
        return "판정 실행 창(그 달 3~7일)"
    if KR_BATCH_AVOID[0] <= kst.time() < KR_BATCH_AVOID[1]:
        return "국내 일일 배치 시각(KST 07:30~10:00)"
    opener = us_open or (lambda d: cal.session_open_utc("US", d))
    for d in {now.astimezone(UTC).date(), (now.astimezone(UTC) + US_BATCH_AVOID).date()}:
        try:
            o = opener(d)
        except Exception:  # noqa: BLE001 — 달력 범위 밖
            o = None
        if o is not None and o - US_BATCH_AVOID <= now <= o:
            return "미국 일일 배치 시각(정규장 앞 2시간)"
    return None


def headroom(client: Any, api_name: str, cap: int, limit: int) -> tuple[str | None, str | None]:
    """(멈출 까닭, 경고). 오늘 카운터가 이미 100% 거나 이 탐침의 상한을 더하면 100% 에 닿으면 멈춘다, 80% 면 경고만
    (CLAUDE.md "80% 도달 시 경고, 100%에서 중단"). 카운터를 못 읽으면 멈출 까닭을 돌려주고 부르지 않는다(25.2207)."""
    if db.usage_blocked_today(client, api_name):
        return "오늘 카운터 100%", None
    try:
        rs = client.execute(
            "SELECT call_count FROM api_usage WHERE api_name = ? AND window_type = 'day' AND window_start = ?",
            [api_name, datetime.now(UTC).strftime("%Y-%m-%d")],
        )
        rows = rs.dicts()
    except Exception:  # noqa: BLE001 — 카운터를 못 읽으면 한도를 모르니 부르지 않는다(보수적, 25.2207)
        return "카운터를 읽지 못함 — 한도를 모르면 부르지 않는다", None
    지금 = int(rows[0].get("call_count") or 0) if rows else 0
    if 지금 + cap >= limit:
        return "상한을 더하면 하루 한도 100% 에 닿는다", None
    if 지금 + cap >= limit * 0.8:
        return None, "상한을 더하면 하루 한도 80% 를 넘는다"
    return None, None


# ----------------------------------------------------------------------------------------------------------------------
# 실행
# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class Deps:
    krx_fetch: Callable[[str, str], Any] = krx.fetch
    dart_corp_codes: Callable[[], Any] = dart.fetch_corp_codes
    http_get: Callable[..., Any] | None = None
    client_factory: Callable[[], Any] | None = None
    sessions: Callable[[date, date], list[date]] = kr_sessions
    anchor: Callable[[], date] = lambda: cal.previous_session("KR")
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    us_open: Callable[[date], datetime | None] | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    dart_key: Callable[[], str] = lambda: config.get("DART_API_KEY") or ""


def _client(deps: Deps) -> Any:
    if deps.client_factory is not None:
        return deps.client_factory()
    from batch.core.client import TursoClient

    return TursoClient()


def say(out: Callable[[str], None], *parts: str) -> None:
    """들여쓴 한 줄 — 조각을 " · " 로 잇는다."""
    out("   " + " · ".join(p for p in parts if p))


def 있음(flag: bool) -> str:
    return "있음" if flag else "없음"


def run_krx(reader: KrxReader, deps: Deps, out: Callable[[str], None]) -> dict[str, Any]:
    """A~F 를 낸다. 돌려주는 것은 DART 단계가 쓸 사라진 종목(코드는 찍지 않는다)."""
    anchor = deps.anchor()
    dates = sample_dates(anchor, deps.sessions)
    out(f"표본 기준일 {len(dates)}개 (2021-09 부터 3·9월 마지막 거래일 + 닻) × 시장 {len(MARKETS)}")
    snaps: dict[str, list[Snapshot]] = {m: [] for m in MARKETS}
    for d in dates:
        for m in MARKETS:
            s = reader.day(m, d)
            if s is not None:
                snaps[m].append(s)
    anchors = {m: snaps[m][-1] for m in MARKETS if snaps[m] and snaps[m][-1].day == anchor}
    받은 = sum(len(v) for v in snaps.values())
    say(out, f"받은 응답 {받은}/{len(dates) * len(MARKETS)}", f"빈 배열 {reader.empty}", f"실패 {reader.failures}")

    # A — 사라진 종목
    out("\n== A. 과거 응답에 그 뒤 사라진 종목 행이 드는가")
    셈, 사라짐 = presence(snaps, anchors)
    for m, c in 셈.items():
        say(out, f"{m}: 지난 표본 종목 {c['지난표본종목']}",
            f"닻에 없음 {c['사라짐']} ({pct(c['사라짐'], c['지난표본종목'])})",
            f"이전상장 {c['이전상장']}")  # fmt: skip
    if not anchors:
        판 = "모름 — 닻 응답 없음"
    elif 사라짐:
        판 = "예 — 과거 응답에 지금 없는 종목 행이 든다"
    else:
        판 = "아니오 — 사라진 종목 행이 없다(생존 종목만 주는 것일 수 있다)"
    say(out, f"판정: {판}")

    # E — 원 응답 칸
    out("\n== E. 원 응답 칸·우선주 규칙")
    키 = frozenset().union(*(s.keys for v in snaps.values() for s in v))
    say(out, f"일별매매정보 키 {len(키)}개: {', '.join(sorted(키)) or '없음'}")
    say(out, f"파싱 행(KrxDailyRow)에 없는 키: {', '.join(sorted(키 - PARSED_DAILY_KEYS)) or '없음'}")
    소속키 = sorted(k for k in 키 if SECTION_KEY_RE.search(k))
    say(out, f"소속부·관리·우선주로 읽힐 키(일별): {', '.join(소속키) or '없음'}",
        f"SECT_TP_NM {있음('SECT_TP_NM' in 키)}")  # fmt: skip
    for m in MARKETS:
        if m not in anchors:
            say(out, f"{m}: 닻 응답 없음 — 우선주 규칙 모름")
            continue
        닻 = anchors[m].rows
        표 = Counter((preferred_by_code(c), preferred_by_name(r.isu_nm)) for c, r in 닻.items())
        say(out, f"{m} 우선주(닻): 둘 다 {표[(True, True)]}", f"코드만 {표[(True, False)]}",
            f"이름만 {표[(False, True)]}", f"둘 다 아님 {표[(False, False)]}",
            f"어긋남 {표[(True, False)] + 표[(False, True)]}")  # fmt: skip
        마스터 = reader.master(m, anchor)
        if 마스터 is None:
            say(out, f"{m} 종목기본정보: 못 받음 — 소속부·주식종류 모름")
            continue
        mk, mrows = 마스터
        say(out, f"{m} 종목기본정보 키 {len(mk)}개", f"SECT_TP_NM {있음('SECT_TP_NM' in mk)}",
            f"KIND_STKCERT_TP_NM {있음('KIND_STKCERT_TP_NM' in mk)}", f"LIST_DD {있음('LIST_DD' in mk)}",
            "마스터는 그날 상장 종목만 — 과거 시점 관리·폐지사 상장일은 여기서 모름")  # fmt: skip
        if "KIND_STKCERT_TP_NM" in mk:
            종류 = {r.ticker: ("우선" in r.share_kind) for r in mrows if r.ticker}
            맞춘 = [c for c in 닻 if c in 종류]
            코드어긋 = sum(1 for c in 맞춘 if preferred_by_code(c) != 종류[c])
            이름어긋 = sum(1 for c in 맞춘 if preferred_by_name(닻[c].isu_nm) != 종류[c])
            say(out, f"{m} 주식종류 대비 어긋남: 코드 규칙 {코드어긋}", f"이름 규칙 {이름어긋}",
                f"맞춘 종목 {len(맞춘)}")  # fmt: skip
        say(out, f"{m} 소속부 값 종류 수 {len({r.section_type for r in mrows if r.section_type})} (값 이름은 안 찍음)")

    # D — MKTCAP 전제·LIST_SHRS
    out("\n== D. MKTCAP ≈ 종가 × LIST_SHRS (같은 날, 0.1% 안) · LIST_SHRS 가 기준일마다 바뀌는가")
    맞음 = 틀림 = 모름 = 0
    날최저: float | None = None
    for v in snaps.values():
        for s in v:
            판들 = [mktcap_match(r) for r in s.rows.values()]
            a, b = sum(1 for x in 판들 if x), sum(1 for x in 판들 if x is False)
            맞음, 틀림, 모름 = 맞음 + a, 틀림 + b, 모름 + sum(1 for x in 판들 if x is None)
            if a + b:
                날최저 = min(1.0 if 날최저 is None else 날최저, a / (a + b))
    say(out, f"맞음 {맞음}", f"틀림 {틀림}", f"칸 빔 {모름}", f"맞음 비율 {pct(맞음, 맞음 + 틀림)}",
        f"가장 낮은 날 {'모름' if 날최저 is None else f'{날최저:.1%}'}")  # fmt: skip
    전제 = "모름" if not 맞음 + 틀림 else ("예" if 틀림 <= 0.01 * (맞음 + 틀림) else "아니오")
    say(out, f"판정(MKTCAP = 그날 실제 시총 전제): {전제} (틀림 ≤ 1% 면 예)")
    바뀐것 = 0
    for m in MARKETS:
        여럿, 바뀜 = list_shares_changes(snaps[m])
        바뀐것 += 바뀜
        say(out, f"{m}: 두 표본 이상 종목 {여럿}", f"LIST_SHRS 바뀐 종목 {바뀜} ({pct(바뀜, 여럿)})")
    say(out, "판정(LIST_SHRS 가 그날 값): " + ("예 — 기준일마다 바뀐다" if 바뀐것 else "모름 — 바뀐 종목이 없다"))

    # C — 정리매매
    out("\n== C. 정리매매 기간 행 (시장마다 사라진 종목 하나, 마지막 관측일까지 하루씩)")
    창들: list[list[Snapshot]] = []
    for m in MARKETS:
        후보 = sorted(((d, c) for (mm, c), d in 사라짐.items() if mm == m), key=lambda x: (-x[0].toordinal(), x[1]))
        if not 후보:
            say(out, f"{m}: 사라진 종목 없음 — 모름")
            continue
        d0, code = 후보[0]
        뒤 = next((s.day for s in snaps[m] if s.day > d0), anchor)
        구간 = deps.sessions(d0, 뒤)

        def 있나(d: date, m: str = m, code: str = code) -> bool | None:
            s = reader.day(m, d)
            return None if s is None else code in s.rows

        i = bisect_edge(구간, 있나) if len(구간) >= 2 else None
        if i is None:
            say(out, f"{m}: 마지막 관측일 모름(상한·실패)")
            continue
        끝 = 구간[i]
        창날 = deps.sessions(끝 - timedelta(days=CLEANUP_WINDOW * 2 + 10), 끝)[-CLEANUP_WINDOW:]
        창 = [reader.day(m, d) for d in 창날]
        창들.append([s for s in 창 if s is not None])
        c = cleanup_shape([s.rows.get(code) if s else None for s in 창])
        say(out, f"{m}: 창 {c['거래일']}거래일", f"행 있음 {c['행있음']}", f"행 없음 {c['행없음']}",
            f"|FLUC_RT|>30% {c['큰등락']}일", f"거래대금 0 {c['거래대금0']}일",
            f"FLUC_RT 대 원 종가 비율 1%p 넘게 갈림 {c['FLUC_원종가_갈림']}일",
            f"이분 탐색 거래일 {len(구간)}")  # fmt: skip
        끝날 = next((s for s in 창 if s is not None and s.day == 끝), None)
        if 끝날 is not None:
            n = sum(1 for r in 끝날.rows.values() if r.change_pct is not None and abs(r.change_pct) > BIG_MOVE_PCT)
            say(out, f"{m}: 그 마지막 관측일 응답의 |FLUC_RT|>30% 행 {n}")
    빈칸 = sum(row_gaps(w) for w in 창들)
    say(out, f"잇단 거래일에서 앞뒤엔 있고 가운데 없는 (날, 종목) {빈칸} — 정지 중 '행 없음' 꼴")

    # B — 정지
    out("\n== B. 정지 중 행 모양 (받은 응답 전체 — 표본일·정리매매 창·이분 탐색 날)")
    h = halted_shape(r for s in reader.cache.values() if s is not None for r in s.rows.values())
    say(out, f"행 {h['행']}", f"거래대금 0 {h['거래대금0']} ({pct(h['거래대금0'], h['행'])})",
        f"그 가운데 시가 0 {h['거래대금0_시가0']}", f"그 가운데 종가 있음 {h['거래대금0_종가있음']}",
        f"거래량 0 {h['거래량0']}", f"종가 빔 {h['종가없음']}")  # fmt: skip

    # F — 분할 기준가
    out("\n== F. FLUC_RT·전일 대비 기준가가 권리락·액면분할 기준가인가")
    기준판: Counter = Counter()
    사건: list[tuple[date, str, str, date, int]] = []
    for m in MARKETS:
        e = split_events(snaps[m])
        say(out, f"{m}: 표본 사이 분할 후보 {len(e)}")
        사건 += [(d_new, code, m, d_old, k) for d_new, code, d_old, k in e]
    사건.sort(key=lambda x: (-x[0].toordinal(), x[1], x[2]))
    for d_new, code, m, d_old, k in 사건[:SPLIT_SAMPLES]:
        old = next(s for s in snaps[m] if s.day == d_old).rows[code]
        구간 = deps.sessions(d_old, d_new)

        def 옛날(d: date, m: str = m, code: str = code, old: KrxDailyRow = old, k: int = k) -> bool | None:
            s = reader.day(m, d)
            if s is None:
                return None
            r = s.rows.get(code)
            return not (r and r.listed_shares and r.listed_shares >= (old.listed_shares or 0) * k * 0.99)

        i = bisect_edge(구간, 옛날) if len(구간) >= 2 else None
        if i is None:
            기준판["모름"] += 1
            continue
        앞, 첫 = reader.cache.get((m, 구간[i])), reader.cache.get((m, 구간[i + 1]))
        기준판[classify_base(앞.rows.get(code) if 앞 else None, 첫.rows.get(code) if 첫 else None, k)] += 1
    say(out, f"분할 후보 {len(사건)}", f"본 것 {sum(기준판.values())}",
        " · ".join(f"{k} {v}" for k, v in sorted(기준판.items())) or "판정 없음")  # fmt: skip
    return {"사라짐": 사라짐, "snaps": snaps}


def run_dart(reader: DartReader, 사라짐: dict[tuple[str, str], date], snaps: dict[str, list[Snapshot]],
             today: date, out: Callable[[str], None]) -> None:  # fmt: skip
    out("\n== G. DART 로 폐지 회사의 상장일·폐지일·폐지 사유를 낼 수 있는가")
    if not 사라짐:
        say(out, "사라진 종목이 없어 볼 것 없음 — 상장일·폐지일·사유 모두 모름")
        return
    codes = reader.corp_codes()
    if codes is None:
        say(out, "고유번호 목록 못 받음 — 모두 모름")
        return
    by_code = {c.stock_code: c for c in codes}
    이름표 = {c.corp_name for c in codes}
    맞음 = sorted(((d, c) for (_, c), d in 사라짐.items() if c in by_code), key=lambda x: (-x[0].toordinal(), x[1]))
    마지막이름 = {}
    for (m, c), d in 사라짐.items():
        s = next((s for s in snaps[m] if s.day == d), None)
        if s and c in s.rows:
            마지막이름[c] = s.rows[c].isu_nm
    이름맞음 = sum(1 for c, n in 마지막이름.items() if c not in by_code and n in 이름표)
    say(out, f"고유번호 목록(종목코드 있는 것) {len(codes)}", f"사라진 종목 {len(사라짐)}",
        f"종목코드로 찾음 {len(맞음)} ({pct(len(맞음), len(사라짐))})",
        f"코드 없이 이름으로만 찾음 {이름맞음}")  # fmt: skip
    키: set[str] = set()
    분류: Counter = Counter()
    폐지제목 = 사유제목 = 본회사 = 0
    for d, c in 맞음[:DART_CORP_SAMPLES]:
        corp = by_code[c].corp_code
        body = reader.company(corp)
        if body is None:
            break
        본회사 += 1
        if str(body.get("status")) == dart.STATUS_OK:
            키 |= set(body)
            분류[str(body.get("corp_cls") or "빔")] += 1
        끝 = min(today, d + timedelta(days=DART_LOOKAHEAD_DAYS))
        lst = reader.exchange_disclosures(corp, d - timedelta(days=DART_LOOKBACK_DAYS), 끝)
        if lst is None:
            break
        제목 = [str(r.get("report_nm") or "") for r in lst.get("list") or []]
        폐지제목 += any(DELIST_TITLE in t for t in 제목)
        사유제목 += any(DELIST_TITLE in t and "사유" in t for t in 제목)
    후보 = sorted(k for k in 키 if LISTING_KEY_RE.search(k))
    say(out, f"본 회사 {본회사}", f"company.json 키 {len(키)}개: {', '.join(sorted(키)) or '없음'}")
    say(out, f"corp_cls 분포: {' · '.join(f'{k} {v}' for k, v in sorted(분류.items())) or '없음'}")
    say(out, f"상장일·폐지일로 읽힐 키: {', '.join(후보) or '없음'}")
    say(out, f"거래소공시(I) 창에 '{DELIST_TITLE}' 제목이 있는 회사 {폐지제목}/{본회사}",
        f"제목에 '사유' 까지 {사유제목}/{본회사}")  # fmt: skip
    say(out, f"판정: 상장일 {'후보 칸 있음 [확인필요: 뜻]' if 후보 else '모름'}",
        f"폐지일 {'공시 접수일로 어림 [확인필요]' if 폐지제목 else '모름'}",
        f"폐지 사유 {'공시 제목으로만 [확인필요: 본문]' if 폐지제목 else '모름'}",
        "KRX 상장폐지 현황 API 모름 [확인필요]")  # fmt: skip
    say(out, f"DART 상태 셈: {' · '.join(f'{k} {v}' for k, v in sorted(reader.status.items())) or '없음'}")


def report_time(krx_b: Budget, out: Callable[[str], None]) -> None:
    out("\n== H. 호출 1회 응답 시간")
    if not krx_b.seconds:
        say(out, "모름 — 부른 것이 없다")
        return
    med = statistics.median(krx_b.seconds)
    say(out, f"KRX {len(krx_b.seconds)}회", f"가운데 {med:.2f}초", f"가장 긴 {max(krx_b.seconds):.2f}초")
    분 = PHASE1_CALLS * (med + PAUSE_SECONDS) / 60
    say(out, f"① 어림: {PHASE1_CALLS}회 × (가운데 + 쉼 {PAUSE_SECONDS}초) ≈ {분:.0f}분")


def main(argv: list[str] | None = None, deps: Deps | None = None) -> int:
    argparse.ArgumentParser(description="u3 생존편향 ⓪ 자료 탐침 (읽기만)").parse_args(argv)
    deps = deps or Deps()
    out = print
    out("u3 생존편향 ⓪ 자료 탐침 — 자료 꼴만 (docs/factors.md 12.45). 종목·가격·금액은 찍지 않는다")
    거절 = window_refusal(deps.now(), deps.us_open)
    if 거절:
        out(f"부르지 않는다: {거절}")
        return 2
    try:
        client = _client(deps)
    except Exception as exc:  # noqa: BLE001 — 카운터 없이 부르지 않는다
        out(f"카운터(Turso)를 열지 못해 부르지 않는다: {type(exc).__name__}")
        return 2
    krx_b = Budget("KRX", KRX_MAX_CALLS, per_call=krx.MAX_RETRY)
    dart_b = Budget("DART", DART_MAX_CALLS)
    try:
        for b, name, limit in ((krx_b, KRX_COUNTER, krx.DAILY_LIMIT), (dart_b, DART_COUNTER, dart.DAILY_LIMIT)):
            까닭, 경고 = headroom(client, name, b.cap, limit)
            if 경고:
                out(f"{b.name}: 경고 — {경고}")
            if 까닭:
                b.stopped = 까닭
                out(f"{b.name}: 부르지 않는다 — {까닭}")
        결과: dict[str, Any] = {"사라짐": {}, "snaps": {m: [] for m in MARKETS}}
        if not krx_b.stopped:
            결과 = run_krx(KrxReader(krx_b, deps.krx_fetch, deps.clock, deps.sleep), deps, out)
        key = deps.dart_key()
        if not dart_b.stopped and key:
            run_dart(DartReader(dart_b, key, deps.http_get, deps.dart_corp_codes, deps.clock),
                     결과["사라짐"], 결과["snaps"], deps.now().astimezone(KST).date(), out)  # fmt: skip
        elif not key:
            out("\n== G. DART_API_KEY 가 비어 모름")
        report_time(krx_b, out)
    finally:
        out("\n== 호출·카운터")
        for b, name, limit in ((krx_b, KRX_COUNTER, krx.DAILY_LIMIT), (dart_b, DART_COUNTER, dart.DAILY_LIMIT)):
            상태 = "안 부름"
            if b.sent:
                try:
                    상태 = db.record_and_guard(client, name, count=b.sent, limit_value=limit, label=f"{b.name}(⓪ 탐침)")
                except Exception as exc:  # noqa: BLE001
                    상태 = f"적기 실패 {type(exc).__name__}"
            out(f"   {b.name}: 보낸 요청 {b.sent}/{b.cap} · 상한에 막힌 부름 {b.refused} · 카운터 {상태}"
                + (f" · 멈춤 {b.stopped}" if b.stopped else ""))  # fmt: skip
        close = getattr(client, "close", None)
        if callable(close):
            close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
