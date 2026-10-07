"""우리 점수를 많이 담은 ETF — 전 종목 비중으로 가중평균한 종합 점수 (docs/etf.md 11.2, docs/infra.md 25.967).

    보유      = 그 ETF 의 가장 새 NPORT-P.  w_i = pctVal (%), 0 이하(공매도·파생 평가손)는 뺀다
    매칭      = CUSIP → 심볼(SEC 결제실패 파일) → 미국 stocks.  이름 매칭은 쓰지 않는다
    S_i       = 그 종목의 가장 새 종합 점수 (미국 점수끼리만 — 국내·미국은 잣대가 다르다)
    덮은 비중 C = Σ w_i (매칭되고 점수가 있는 종목)
    점수 담음 A = Σ w_i · S_i / C
    시장 대비  = A − A(VTI)
    추천 비중 R = Σ w_i (가장 새 장기 신호 종목) — 참고. 순위에 쓰지 않는다
    상위 비중 T = Σ w_i (미국 점수 상위 `TOP_SHARE_PCT`% 종목)  ← **순위 기준** (11.5, 25.968)
    집중 배수  = T ÷ T(VTI)

C 가 `COVERAGE_MIN_PCT` 미만이면 A·T 순위를 내지 않는다 — 점수 없는 몫이 크면 바구니를 대표하지 못한다.
보수·규모 점수(8.3)는 바꾸지 않는다. 백테스트 전이라 따로 줄 세운 참고 순위다(CLAUDE.md 기법 루프 4단계).

**왜 평균(A)이 아니라 상위 비중(T)으로 줄 세우나** (25.968, 사용자 지적 "대표지수 ETF만 추천하면 …
아무나 다 대표지수정도는 추천할 수 있어"). 첫 운영 실행에서 A 는 넓은 바구니일수록 시장 평균으로 수렴해
VTI 52.4 와 1위가 +2.7 차이뿐이었다.
"우리가 고른 종목을 많이 담았나" 는 평균이 아니라 **상위 종목에 비중을 얼마나 실었나**다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

#: A 를 내는 덮은 비중 하한(%). `[확인필요: 실측 뒤 조정]` — 실측(2026-10-06) 미국 넓은 지수 ETF 는 CUSIP 매칭만
#: 97~99.6% 였고, 점수는 유니버스(시총·거래대금 하한) 안 종목에만 있어 그보다 낮을 것이다. 3할 넘게 점수 없는 몫이면
#: 평균이 바구니를 대표한다고 보지 않는다 — 추정값이라 첫 운영 실행의 덮은 비중을 보고 고친다
COVERAGE_MIN_PCT = 70.0
#: 근거표에 적는 기여 상위 종목 수 (w·S 순)
TOP_CONTRIBUTORS = 10
#: "우리 상위 종목" 의 범위 — 그날 미국 종합 점수가 있는 종목 가운데 상위 이 % `[확인필요: 실측 뒤 조정]`.
#: 10% 는 유니버스 약 1,800종목 중 180종목 남짓 — 1부 추천 다섯보다 넓어 바구니 비교가 흔들리지 않고, 시장 절반보다 좁아
#: 고른 것과 안 고른 것을 가른다
TOP_SHARE_PCT = 10.0
#: 후보 풀(11.5) — 핵심 판정의 넓은 지수 조건 없이, 주식 비중·순자산만 본다.
#: 레버리지·인버스는 핵심 판정과 같은 규칙으로 뺀다
POOL_MIN_STOCK_POSITION = 0.8
POOL_MIN_ASSETS_USD = 100_000_000
#: 후보 풀에서 빼는 야후 분류 — 옵션을 팔거나 사서 수익 모양을 바꾼 전략 상품 (25.969).
#: 국내 판정이 커버드콜·버퍼·옵션을 빼는 것과 같은 이유(오래 들고 가며 복리로 불리는 적립과 목적이 반대).
#: 2026-10-06 운영 실측: Derivative Income 31개(JEPI·QYLD·AIPI …)·Defined Outcome 8개(BUFR …)가 풀에 들어와
#: AIPI 가 17위에 올랐다
POOL_EXCLUDED_CATEGORIES = ("Derivative Income", "Defined Outcome")
#: 시장 대비의 기준 — 미국 전체 시장 ETF
MARKET_BENCHMARK = "VTI"
#: 국내 지수 ETF 의 시장 대비 기준 — KODEX 200 (25.974). 국내 점수끼리만 견준다
KR_BENCHMARK = "069500"
KR_BENCHMARK_NAME = "KODEX 200"
#: 국내 상장 해외지수 ETF 의 대리 보유 — 같은 기초지수를 따르는 미국 ETF (docs/etf.md 11.3 표와 같다)
#: 2026-10-06 넓힘 (25.970) — 국내 상장 ETF 의 기초지수 이름(한국거래소 `IDX_IND_NM`, 운영 DB 순자산 100억원 이상 목록)
#: 가운데 **같은 지수를 따르는 미국 ETF 가 분명한 것**만.
#: 지수 이름만 비슷한 것(예: "PHLX US AI Semiconductor")은 넣지 않는다
PROXY_BY_INDEX = {
    "S&P 500": "IVV",
    "NASDAQ 100": "QQQ",
    "Dow Jones U.S. Dividend 100 Index(TR)": "SCHD",
    "Dow Jones U.S. Dividend 100 Price Return Index": "SCHD",
    "PHLX Semiconductor Sector Index": "SOXX",
    "MVIS US Listed Semiconductor 25 Index": "SMH",
    "CRSP US Large Cap Growth Index(PR)": "VUG",
    "나스닥 종합주가지수": "ONEQ",
}
#: 미국 단위형 신탁(UIT) → 같은 지수의 미국 ETF (docs/etf.md 11.5, docs/infra.md 25.982). SEC 펀드 티커 목록
#: (`company_tickers_mf.json`)에 시리즈가 없어 N-PORT 를 찾지 못한다 — 2026-10-06 운영 세 번 모두 SPY·MDY·DIA 가
#: "보유를 받지 못한 ETF" 였다. 같은 지수를 완전 복제하는 ETF 의 보유로 대신 본다(국내 상장 해외지수 11.3 과 같은 방식).
#: DIA(다우 30, 가격 가중)는 같은 지수를 따르는 큰 ETF 가 없어 넣지 않는다 — 계속 "받지 못함" 으로 남는다
US_UIT_PROXY = {"SPY": "IVV", "MDY": "IJH"}
#: 국내 상장 후보 풀의 순자산 하한(원) — 미국 풀(1억달러)처럼 핵심 하한(1,000억원)의 10분의 1 `[확인필요: 실측 뒤 조정]`
POOL_MIN_ASSETS_KRW = 10_000_000_000
#: 대리 보유를 쓰지 않는 국내 상품 이름 표기 (25.971). 액티브는 지수를 **비교 기준**으로만 써 실제 보유가 다르고
#: (운영 실측: "TIGER 글로벌이노베이션액티브" 의 기초지수가 NASDAQ 100 이라 QQQ 보유로 14위에 올랐다), 선물형은 주식을
#: 들고 있지 않다. 둘 다 "같은 지수의 미국 ETF 보유" 가 그 상품의 보유라고 말할 수 없다
PROXY_EXCLUDED_NAME_MARKERS = ("액티브", "선물")


def norm_symbol(sym: str | None) -> str:
    """심볼 비교용 — 대문자, 점·대시·빗금 제거 (BRK.B · BRK-B · BRK/B → BRKB)."""
    return "".join(ch for ch in (sym or "").upper() if ch.isalnum())


@dataclass(frozen=True)
class Contributor:
    stock_id: int
    symbol: str
    name: str
    weight_pct: float
    score: float


@dataclass(frozen=True)
class Tilt:
    avg_score: float | None  # A. 덮은 비중이 모자라면 None
    coverage_pct: float  # C
    matched_pct: float  # 우리 종목으로 이은 비중(점수 유무와 무관)
    total_pct: float  # 비중 합(양수만)
    rec_pct: float | None  # R. 신호 목록을 못 읽었으면 None
    top_pct: float  # T — 우리 점수 상위 종목 비중
    holdings: int
    top: list[Contributor]

    def as_dict(self) -> dict:
        return {**asdict(self), "top": [asdict(c) for c in self.top]}


def compute(
    holdings: list[tuple[str, float]],
    cusip_to_symbol: dict[str, str],
    stock_by_symbol: dict[str, tuple[int, str, str]],
    score_of: Callable[[int], float | None],
    rec_ids: set[int] | None,
    top_ids: set[int] | None = None,
) -> Tilt:
    """holdings = [(CUSIP, w%)]. stock_by_symbol = 정규화 심볼 → (stock_id, 심볼, 이름)."""
    total = matched = covered = weighted = rec = top_w = 0.0
    contrib: dict[int, Contributor] = {}
    for cusip, w in holdings:
        if w <= 0:
            continue
        total += w
        sym = cusip_to_symbol.get(cusip)
        stock = stock_by_symbol.get(norm_symbol(sym)) if sym else None
        if stock is None:
            continue
        matched += w
        sid = stock[0]
        if rec_ids is not None and sid in rec_ids:
            rec += w
        if top_ids is not None and sid in top_ids:
            top_w += w
        s = score_of(sid)
        if s is None:
            continue
        covered += w
        weighted += w * s
        prev = contrib.get(sid)  # 같은 종목이 여러 줄(클래스주가 아니라 같은 CUSIP 의 분할 기재)이면 합친다
        contrib[sid] = Contributor(sid, stock[1], stock[2], (prev.weight_pct if prev else 0.0) + w, s)
    avg = weighted / covered if covered > 0 and covered >= COVERAGE_MIN_PCT else None
    top = sorted(contrib.values(), key=lambda c: -(c.weight_pct * c.score))[:TOP_CONTRIBUTORS]
    return Tilt(
        avg_score=avg, coverage_pct=covered, matched_pct=matched, total_pct=total,
        rec_pct=rec if rec_ids is not None else None, top_pct=top_w, holdings=len(holdings), top=top,
    )  # fmt: skip


def top_ids_of(scores: dict[int, float], pct: float = TOP_SHARE_PCT) -> tuple[set[int], float | None]:
    """점수 상위 pct% 종목과 그 문턱 점수. 동점은 함께 들어간다(문턱 이상)."""
    if not scores:
        return set(), None
    ordered = sorted(scores.values(), reverse=True)
    k = max(1, int(len(ordered) * pct / 100))
    cut = ordered[k - 1]
    return {sid for sid, v in scores.items() if v >= cut}, cut


def rank_by(rows: list[tuple[int, float | None, float]]) -> dict[int, tuple[int, int]]:
    """[(pick_id, 값, 동점 가르기)] 를 값 내림차순, 같으면 동점 가르기 내림차순으로. 값이 없으면 순위 없음.

    국내 상장은 같은 지수를 따르는 여러 상품이 같은 대리 보유라 값이 같다 — 순자산 큰 순으로 가른다."""
    scored = sorted([r for r in rows if r[1] is not None], key=lambda r: (-(r[1] or 0.0), -r[2], r[0]))
    return {pid: (i, len(scored)) for i, (pid, _, _) in enumerate(scored, start=1)}


def rank_within(groups: dict[str, list[tuple[int, float | None]]]) -> dict[int, tuple[int, int]]:
    """분류 → [(pick_id, A)] 에서 A 내림차순 순위. A 가 없는 것은 순위가 없다.

    반환 pick_id → (순위, 분류 안 A 있는 수)."""
    out: dict[int, tuple[int, int]] = {}
    for rows in groups.values():
        scored = sorted([(pid, a) for pid, a in rows if a is not None], key=lambda x: -x[1])
        for i, (pid, _) in enumerate(scored, start=1):
            out[pid] = (i, len(scored))
    return out
