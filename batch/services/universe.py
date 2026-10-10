"""종목 유니버스 구성.

CLAUDE.md 의 제외 규칙을 판정한다.
  관리종목, 거래정지, 스팩, 상장 1년 미만,
  시총 하한 미만, 20일 평균 거래대금 하한 미만

판정은 순수 함수로 두고 DB 접근과 분리한다. 규칙이 틀리면 유니버스 전체가
틀어지므로 고정 데이터로 검증할 수 있어야 한다.

제외된 종목도 사유와 함께 남긴다. "왜 이 종목이 빠졌는가"를 나중에 물을 수
있어야 하기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# 제외 사유. 화면과 리포트에 그대로 쓰인다.
REASON_SUPERVISED = "관리종목"
REASON_HALTED = "거래정지"
#: 관리종목·거래정지와 같은 무리지만 **다른 지정**이다. 예전에는 소속부 글자에 따라 "관리종목"·"거래정지" 로
#: 뭉뚱그려 적어 제외 사유가 부정확했다 (docs/infra.md 25.425). 빼는 것은 같고 이름만 바로 적는다
REASON_CAUTION = "투자주의환기"
REASON_LIQUIDATION = "정리매매"
REASON_SPAC = "스팩"
REASON_NEWLY_LISTED = "상장1년미만"
REASON_SMALL_CAP = "시총미달"
REASON_LOW_TURNOVER = "거래대금미달"
REASON_NOT_COMMON = "보통주아님"
REASON_NOT_STOCK = "주권아님"
REASON_NO_DATA = "데이터없음"
#: 종목 마스터에서 빠진 종목 (docs/infra.md 25.412). 지난 스냅샷에 있던 종목은 다음 스냅샷에도 이 사유로
#: 한 줄 남긴다 — 예전에는 `status = 'active'` 만 판정해 **사유 없이 사라졌다**
REASON_DELISTED = "상장폐지"
REASON_MASTER_EXCLUDED = "마스터제외"
#: stocks.status → 사유
STATUS_REASON = {"delisted": REASON_DELISTED, "excluded": REASON_MASTER_EXCLUDED}

ALL_REASONS = [
    REASON_SUPERVISED,
    REASON_HALTED,
    REASON_CAUTION,
    REASON_LIQUIDATION,
    REASON_SPAC,
    REASON_NEWLY_LISTED,
    REASON_SMALL_CAP,
    REASON_LOW_TURNOVER,
    REASON_NOT_COMMON,
    REASON_NOT_STOCK,
    REASON_NO_DATA,
    REASON_DELISTED,
    REASON_MASTER_EXCLUDED,
]


#: 상장 경과일 하한의 기본값 (CLAUDE.md 유니버스 규칙: "상장 1년 미만" 제외).
#: **`jobs/us_shares` 도 이 값을 본다** — 관측 이력이 이보다 얕은데 첫 관측일을
#: `listed_date` 로 적으면 **미국 전 종목이 '상장 1년 미만' 으로 빠진다**
#: (docs/infra.md 25.166). 두 곳이 갈라지면 안 되므로 정의처를 여기 하나로 둔다.
DEFAULT_MIN_LISTED_DAYS = 365


@dataclass(frozen=True)
class UniverseFilters:
    """판정 기준. 값의 표는 docs/design.md 1.2 (docs/infra.md 25.304).

    **설정 화면과 연결되어 있지 않다.** 예전 주석은 "설정에서 조정한다" 고 했지만 그런 설정 키가 없다.
    """

    min_market_cap: int  # 원 또는 달러
    min_avg_turnover_20d: int
    min_listed_days: int = DEFAULT_MIN_LISTED_DAYS
    exclude_preferred: bool = True

    @classmethod
    def korea(cls) -> UniverseFilters:
        # CLAUDE.md 기본값: 시총 1,000억
        # 거래대금 하한은 명시가 없어 5억으로 둔 출발값이다 [확인필요: 실측 뒤 조정]
        return cls(min_market_cap=100_000_000_000, min_avg_turnover_20d=500_000_000)

    @classmethod
    def usa(cls) -> UniverseFilters:
        # CLAUDE.md 기본값: 시총 10억 달러
        return cls(min_market_cap=1_000_000_000, min_avg_turnover_20d=5_000_000)


@dataclass
class Candidate:
    """판정 대상 한 종목. 어느 시장이든 이 형태로 맞춰 넣는다."""

    ticker: str
    name: str
    market: str
    security_group: str = ""  # 증권구분. 국내만 채워진다
    section_type: str = ""  # 소속부. 관리종목이 여기 나타난다
    share_kind: str = ""  # 보통주 우선주
    listed_date: str | None = None  # YYYY-MM-DD
    market_cap: int | None = None
    avg_turnover_20d: int | None = None
    #: **지금 이것을 채우는 수집기가 없다** (2026-09-22 확인, docs/infra.md 25.129).
    #: 국내 거래정지는 아래 `section_type` 의 글자로 잡고 있고, 미국은 잡을 방법이 없다.
    #: 자리를 남겨 두는 것은 KIS·거래소가 정지 플래그를 주게 되면 여기로 들어오기 때문이다.
    #: **이 필드가 있다고 거래정지를 보고 있다고 여기면 안 된다.**
    is_halted: bool = False
    #: 상장일이 **우리 수집 시작의 가장자리**다 (docs/infra.md 25.652, 감사). 미국 상장일은 첫 관측일이라
    #: 수집을 늦게 시작한 종목은 실제 상장일이 그보다 이르다 — 그 날로 "상장 1년 미만" 을 자르면 1년 동안 틀리게 빠진다.
    #: 참이면 상장일 규칙은 **모름이되 자르지 않음**으로 본다(백테스트 `pit_universe` 와 같은 처리)
    listed_at_history_edge: bool = False


@dataclass
class Verdict:
    included: bool
    reason: str | None
    listed_days: int | None


# 소속부에 이 말이 들어가면 관리종목으로 본다.
# 거래소가 쓰는 표기를 그대로 받으므로 포함 여부로 판정한다.
SUPERVISED_MARKERS = ("관리", "투자주의환기")
HALT_MARKERS = ("거래정지", "정리매매")

# 증권구분이 이것이 아니면 일반 주식이 아니다.
# 부동산투자회사, 수익증권, 외국주권 등은 재무 비교의 잣대가 달라 뺀다.
STOCK_GROUPS = ("주권",)


#: **그 시장에서 보지 못하는 검사와 그 사유** (docs/infra.md 25.129).
#:
#: CLAUDE.md 의 유니버스 제외 규칙은 시장을 가리지 않는다("관리종목, 거래정지, 스팩, …").
#: 그런데 위의 판정은 **국내 거래소가 주는 열**(`section_type`·`security_group`·이름)에
#: 기대고 있고, 미국 마스터(나스닥 심볼 디렉터리)에는 그런 열이 없다.
#:
#: 그래서 미국 스냅샷의 제외 사유에 "관리종목" 이 한 건도 안 나온다. 그것은
#: **"없다" 가 아니라 "안 본다"** 다. 둘을 같은 모양으로 두면 화면이 거짓말을 한다 —
#: 이 저장소가 `d1_writes_today`·`database_size` 에서 되풀이해 지켜 온 규칙과 같다(25.55).
#:
#: 값은 (검사 이름, 왜 못 보나). **사유 없는 예외를 두지 않는다.**
NOT_CHECKED: dict[str, tuple[tuple[str, str], ...]] = {
    "KR": (),
    "US": (
        (
            REASON_SUPERVISED,
            "나스닥 심볼 디렉터리에 소속부·관리종목 열이 없다"
            " [확인필요: nasdaqlisted.txt 의 Financial Status 열(D/E/Q 등)로 대신할 수 있는지]",
        ),
        (
            REASON_HALTED,
            "같은 이유로 거래정지 표시가 없다. 야후 시세가 멈추는 것으로는"
            " 짧은 정지와 휴장을 구별할 수 없다 [확인필요]",
        ),
        (
            REASON_SPAC,
            "국내 스팩은 이름에 '스팩' 이 들어가 확실하지만, 미국은 'Acquisition Corp'"
            " 가 스팩이 아닌 회사에도 쓰인다. 오탐률을 재 보지 않고 넣지 않는다 [확인필요]",
        ),
        (
            REASON_NOT_STOCK,
            "증권구분 열이 없다. 대신 마스터를 넣을 때 ETF·테스트 종목·우선주를"
            " 이름과 심볼 모양으로 걸러 `status='excluded'` 로 둔다"
            " (`jobs/universe.us_exclusion_statements`) — 판정이 아니라 수집 단계에서 뺀다",
        ),
    ),
}


def not_checked(country: str) -> tuple[tuple[str, str], ...]:
    """그 나라에서 **하지 못하는** 제외 검사들. 없으면 빈 튜플."""
    return NOT_CHECKED.get(country.upper(), ())


def not_checked_warning(country: str) -> str | None:
    """운영자가 읽을 한 줄. 0 건과 "안 봄" 을 구별해 준다."""
    빠진 = not_checked(country)
    if not 빠진:
        return None
    이름 = " · ".join(name for name, _ in 빠진)
    return f"{country} 는 {이름} 을(를) 판정하지 않습니다 — 제외 0건은 '없다' 가 아니라 '안 본다' 입니다"


def marker_warning(
    country: str, verdicts: list[Verdict], section_types: list[str], markets: list[str] | None = None
) -> str | None:
    """**국내 관리종목·거래정지 판정이 정말 걸리는가** (docs/infra.md 25.157).

    그 둘은 한국거래소가 주는 소속부(`SECT_TP_NM`) 글자 하나에 매달려 있다.

        if _contains_any(candidate.section_type, HALT_MARKERS): ...
        if _contains_any(candidate.section_type, SUPERVISED_MARKERS): ...

    그 열에 "관리"·"거래정지" 가 실제로 오는지는 **확인된 적이 없다** `[확인필요]`.
    안 오면 두 검사는 늘 거짓이고, 제외 0건이 "그런 종목이 없다" 로 읽힌다 —
    미국에서 `not_checked_warning` 이 막으려는 바로 그 오해가 국내에도 생긴다(25.131).

    확인할 길은 **한 번 돌려 보는 것**뿐이라, 돌 때 스스로 답하게 한다. 둘 다 0건이면
    그때 실제로 본 소속부 값을 함께 적는다 — 다음 사람이 한 눈에 판단한다.

    **0건이라고 고장이라 말하지 않는다.** 정말 없는 날도 있다. 모른다고 말할 뿐이다.
    """
    if country.upper() != "KR":
        return None
    # **시장마다 따로 센다** (docs/infra.md 25.1111, 유니버스 감사 재현). 코스닥에 `관리종목(소속부없음)` 한 건만 걸려도
    # 관리종목 경고가 꺼져, 소속부가 비어 오는 것으로 기억하는 코스피는 관리종목이 한 번도 걸리지 않는데 말이 없었다
    if markets is not None and len(set(markets)) > 1:
        말들 = []
        for 시장 in sorted(set(markets)):
            골라 = [i for i, m in enumerate(markets) if m == 시장]
            말 = marker_warning(country, [verdicts[i] for i in 골라], [section_types[i] for i in 골라])
            if 말:
                말들.append(f"[{시장}] " + 말)
        return " / ".join(말들) or None
    # **검사마다 따로 센다** (docs/infra.md 25.612, 감사). 예전에는 둘을 합쳐 0건일 때만 말해, 코스닥 관리종목이
    # 한 건만 걸려도 경고가 꺼졌다 — 거래정지를 전혀 못 보는 것이 가려졌다
    # 형제 사유(투자주의환기·정리매매)로는 채우지 않는다 (25.614, 교차검증) — 그 글자는 "관리"·"거래정지" 가 오는지를
    # 말해 주지 않는다. 정리매매 한 건이 거래정지 공백을 가렸다
    검사 = [
        (REASON_SUPERVISED, SUPERVISED_MARKERS[0], (REASON_SUPERVISED,)),
        (REASON_HALTED, HALT_MARKERS[0], (REASON_HALTED,)),
    ]
    빠진 = [(이름, 표지) for 이름, 표지, 사유들 in 검사 if not any(v.reason in 사유들 for v in verdicts)]
    if not 빠진:
        return None
    본것 = sorted({t.strip() for t in section_types if t and t.strip()})
    보기 = ", ".join(본것[:8]) + ("…" if len(본것) > 8 else "") if 본것 else "(전부 비어 있음)"
    return (
        f"국내 {'·'.join(이름 for 이름, _ in 빠진)} 제외가 0건입니다."
        f" 소속부에서 본 값: {보기}."
        f" 여기에 {'·'.join(repr(표지) for _, 표지 in 빠진)} 가 안 보이면 그 검사는"
        " 늘 거짓입니다 — 그때 0건은 '없다' 가 아니라 '안 본다' 입니다 [확인필요]"
    )


def _listed_days(listed_date: str | None, as_of: date) -> int | None:
    if not listed_date:
        return None
    try:
        listed = datetime.strptime(listed_date, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (as_of - listed).days


def judge(
    candidate: Candidate, filters: UniverseFilters, as_of: date
) -> Verdict:
    """한 종목을 판정한다.

    사유는 하나만 남긴다. 가장 먼저 걸린 것이다.
    순서는 "명백한 결격"부터 "수치 미달"로 간다. 관리종목이면서 시총도 작으면
    관리종목이 더 본질적인 사유이기 때문이다.
    """
    days = _listed_days(candidate.listed_date, as_of)

    def out(reason: str) -> Verdict:
        return Verdict(included=False, reason=reason, listed_days=days)

    if candidate.is_halted or _contains_any(candidate.section_type, HALT_MARKERS):
        return out(REASON_LIQUIDATION if "정리매매" in candidate.section_type else REASON_HALTED)

    if _contains_any(candidate.section_type, SUPERVISED_MARKERS):
        # "관리" 가 함께 있으면 관리종목이 먼저다 — 더 본질적인 사유다
        if "관리" not in candidate.section_type and "투자주의환기" in candidate.section_type:
            return out(REASON_CAUTION)
        return out(REASON_SUPERVISED)

    if is_spac(candidate.name):
        return out(REASON_SPAC)

    if candidate.security_group and candidate.security_group not in STOCK_GROUPS:
        return out(REASON_NOT_STOCK)

    if (
        filters.exclude_preferred
        and candidate.share_kind
        and candidate.share_kind != "보통주"
    ):
        return out(REASON_NOT_COMMON)

    # **아는 값으로 미달이면 그 사유를 먼저 적는다** (docs/infra.md 25.612, 감사). 상장일을 모르면 시총·거래대금을
    # 보기 전에 "데이터없음" 이었다 — 미국은 상장일·시총을 거래대금 후보에게만 채워(`us_shares`) 거래대금이 명백히
    # 미달인 수천 종목이 "데이터없음" 으로 적혀, 사유 분포가 쓸모없고 "시세가 비었다" 는 진짜 경보와 섞였다.
    # 제외 여부는 같다 — 모르는 값이 하나라도 있으면 여전히 제외(보수적). 순서도 예전과 같다(상장일 → 시총 → 거래대금)
    수치 = [
        # 가장자리 상장일은 "적어도 그날 전" 일 뿐이라 상장일 칸을 빼고 본다 (25.652)
        *([] if candidate.listed_at_history_edge else [(days, filters.min_listed_days, REASON_NEWLY_LISTED)]),
        (candidate.market_cap, filters.min_market_cap, REASON_SMALL_CAP),
        (candidate.avg_turnover_20d, filters.min_avg_turnover_20d, REASON_LOW_TURNOVER),
    ]
    for 값, 문턱, 사유 in 수치:
        if 값 is not None and 값 < 문턱:
            return out(사유)
    if any(값 is None for 값, _, _ in 수치):
        return out(REASON_NO_DATA)

    return Verdict(included=True, reason=None, listed_days=days)


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    if not text:
        return False
    return any(marker in text for marker in markers)


def is_spac(name: str) -> bool:
    """스팩 판정.

    국내 스팩은 이름에 '스팩'이 들어간다. 거래소가 별도 구분값을 주지 않아
    이름으로 판정한다. 일반 종목 이름에 '스팩'이 들어가는 경우는 없다.
    """
    return "스팩" in (name or "")


def summarize(verdicts: list[Verdict]) -> dict[str, int]:
    """편입 수와 사유별 제외 수를 센다. 리포트와 화면에 쓴다."""
    counts: dict[str, int] = {"편입": 0}
    for verdict in verdicts:
        if verdict.included:
            counts["편입"] += 1
        else:
            key = verdict.reason or REASON_NO_DATA
            counts[key] = counts.get(key, 0) + 1
    return counts
