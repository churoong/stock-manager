"""ETF 전술 계좌 판 2 `etf_tactical_v2` — 월말 10개월선·12개월 수익 부호 (docs/factors.md 12.48 (나) 사전 등록).

**꺼진 채다.** 운영 경로(`jobs/paper_etf`·`jobs/paper`·`desk`)는 이 파일을 부르지 않는다. 판 1(`etf_tactical`)은
그대로이고, 판 2 마이그레이션은 판 1 의 126번째 평가 판정 뒤다(12.48 "순서").
여기는 순수 함수·리플레이·홀드아웃 문뿐이다.

    끝난 월말 = as_of 의 달보다 앞선 달의 마지막 지수 행 (계열 내용이 아니라 as_of 날짜로 가른다)
    ① 10개월 평균 = 끝난 월말을 포함한 최근 10개 월말 종가의 산술평균 (Faber 2007)
    ② 끝난 월말 종가 ≥ 평균                         → 상승: 지수 ETF 98% (`etf_tactical.LONG_WEIGHT`)
    ③ 아니면서 12개월 수익(끝난 월말 ÷ 12개 월말 전 − 1) < 0 → 하락: 인버스 50% (`etf_tactical.INVERSE_WEIGHT`)
       아니면 현금 (상태 `etf_tactical.MIXED` — 10개월선 아래인데 12개월 수익은 음이 아님)
    ④ 모름 → 현금: 끝난 월말 10개 미만 · as_of − 마지막 행 > 7일(`STALE_DAYS`) · 하락인데 13개 월말이 모자람
    ⑤ 달 안에서는 바뀌지 않는다(끝난 월말이 같다). 다만 묵음이 이긴다 — 그날은 모름 → 현금, 풀리면 그달 판정
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from batch.services import etf_tactical as et
from batch.services.metrics import years_between

RULE = "etf_tactical_v2"
#: ① 10개월 평균의 월말 수 (Faber 2007 원형)
MA_MONTHS = 10
#: ③ 12개월 수익 — 끝난 월말과 12개 월말 전, 그래서 이어진 월말 13개가 있어야 인버스가 열린다
RET_MONTHS = 12
#: 갈아탈 때 바뀐 비중 1 당 비용 — 판 1 `replay(cost=0.0012)` 그대로 (12.48 "매매·비용은 판 1 그대로")
COST = 0.0012
#: 문 (c)·계좌 판정 — |MDD_판2| ≤ 0.7 × |MDD_보유| (판 1 의 근거 없는 첫 값 `[확인필요]`, desk.py PREREG (X,1))
MDD_RATIO = 0.7
#: 계좌 판정 — 시간가중 수익 ≥ 보유 − 5%p (판 1 과 같은 첫 값 `[확인필요]`)
RETURN_SLACK_PP = 5.0
#: 계좌 판정 평가 수 — 126번째, 국면 전환 0번(퇴화)이면 252번째에 한 번만 다시
JUDGE_EVAL = 126
REJUDGE_EVAL = 252
#: 홀드아웃(문) 자료 시작 — 야후 ^KS11·^GSPC 2000-01~ (받을 수 있는 시작일은 `[확인필요]`)
HOLDOUT_FROM = "2000-01-01"
#: 운영 `index_prices` 첫 달 (paper.md 17.3 표) — 정확한 첫 행 날짜는 `[확인필요]` 라 문 함수는 날짜를 인자로 받는다
OPERATING_FIRST_MONTH = {"X": "2019-05", "Y": "2019-08"}
#: 통과해도 '문헌 표본 안' 표시를 다는 계좌 (S&P 2000~2008 은 Faber 2007·MOP 2012 표본과 겹친다)
LITERATURE_SAMPLE = frozenset({"Y"})

UP, DOWN, MIXED, UNKNOWN = et.UP, et.DOWN, et.MIXED, et.UNKNOWN

Series = Sequence[tuple[str, float | None]]


@dataclass(frozen=True)
class RegimeV2:
    state: str
    as_of: str  # 판정일(실행일)
    month_end: str | None  # 판정에 쓴 끝난 월말(지수 행 날짜)
    last_row: str | None  # as_of 앞 마지막 지수 행 — 묵음 기준
    close: float | None  # 끝난 월말 종가
    ma10: float | None
    ret12: float | None
    reason: str | None = None  # 모름의 까닭

    @property
    def label(self) -> str:
        return et.LABEL[self.state]


def _clean(series: Series) -> list[tuple[str, float]]:
    return sorted((d[:10], float(c)) for d, c in series if c is not None and float(c) > 0)


def _month(d: str) -> int:
    """'YYYY-MM-DD' → 달 번호(연 × 12 + 월 − 1) — 이어진 달인지 셈하려고"""
    return int(d[:4]) * 12 + int(d[5:7]) - 1


def _month_start(m: int) -> str:
    """달 번호 → 그 달 1일 'YYYY-MM-01'"""
    return f"{m // 12:04d}-{m % 12 + 1:02d}-01"


def month_ends(series: Series, as_of: str) -> list[tuple[str, float]]:
    """**끝난 월말** 목록 — as_of 의 달보다 앞선 달마다 그 달의 마지막 지수 행 (날짜, 종가), 오래된 것부터.

    as_of 의 달(아직 덜 찬 달)은 넣지 않는다. 계열에 아예 없는 달은 목록에도 없다 — 이어졌는지는 `_consecutive`"""
    이번달 = _month(as_of)
    끝: dict[int, tuple[str, float]] = {}
    for d, c in _clean(series):
        if _month(d) < 이번달:
            끝[_month(d)] = (d, c)  # 정렬돼 있어 마지막에 쓴 것이 그 달 마지막 행
    return [끝[m] for m in sorted(끝)]


def _consecutive(ends: list[tuple[str, float]], n: int, as_of: str) -> bool:
    """마지막 n 개 월말이 빈 달 없이 이어지고, 마지막이 as_of 바로 앞 달인가.

    빈 달이 끼면 '최근 10개월'·'12개월 전' 이 달력과 어긋나므로 모름으로 본다(보수적 — 12.48 에 적히지 않은 경우)"""
    if len(ends) < n:
        return False
    달 = [_month(d) for d, _c in ends[-n:]]
    return 달[-1] == _month(as_of) - 1 and all(b - a == 1 for a, b in zip(달, 달[1:], strict=False))


def ret12(series: Series, as_of: str) -> float | None:
    """끝난 월말 종가 ÷ 12개 월말 전 종가 − 1. 이어진 월말 13개가 없으면 None (`etf_theme` 도 같은 정의를 쓴다)"""
    ends = month_ends(series, as_of)
    if not _consecutive(ends, RET_MONTHS + 1, as_of):
        return None
    return ends[-1][1] / ends[-1 - RET_MONTHS][1] - 1


def regime_v2(series: Series, as_of: str) -> RegimeV2:
    """`as_of`(실행일) 판정. `series` 는 (날짜, 종가) — 순서는 묻지 않는다. **as_of 당일과 그 뒤 행은 보지 않는다**
    (운영은 그달 첫 거래일 아침, 오늘 행 없이 판정한다 — 리플레이도 같은 행을 보게 한다)."""
    as_of = as_of[:10]
    줄 = [(d, c) for d, c in _clean(series) if d < as_of]
    if not 줄:
        return RegimeV2(UNKNOWN, as_of, None, None, None, None, None, "지수 없음")
    마지막 = 줄[-1][0]
    if (date.fromisoformat(as_of) - date.fromisoformat(마지막)).days > et.STALE_DAYS:
        return RegimeV2(UNKNOWN, as_of, None, 마지막, None, None, None, f"지수 묵음 {et.STALE_DAYS}일 넘음")
    ends = month_ends(줄, as_of)
    if not _consecutive(ends, MA_MONTHS, as_of):
        why = f"끝난 월말 {len(ends)}개" if len(ends) < MA_MONTHS else "끝난 월말이 이어지지 않음(빈 달)"
        return RegimeV2(UNKNOWN, as_of, ends[-1][0] if ends else None, 마지막, None, None, None, why)
    월말, 종가 = ends[-1]
    ma = sum(c for _d, c in ends[-MA_MONTHS:]) / MA_MONTHS
    r12 = ends[-1][1] / ends[-1 - RET_MONTHS][1] - 1 if _consecutive(ends, RET_MONTHS + 1, as_of) else None
    if 종가 >= ma:
        return RegimeV2(UP, as_of, 월말, 마지막, 종가, ma, r12)
    if r12 is None:
        return RegimeV2(UNKNOWN, as_of, 월말, 마지막, 종가, ma, None, "하락인데 12개월 수익을 낼 월말 13개가 모자람")
    return RegimeV2(DOWN if r12 < 0 else MIXED, as_of, 월말, 마지막, 종가, ma, r12)


def v1_regime(rows: Series, as_of: str) -> et.Regime:
    """판 1 을 같은 리플레이에 넣는 어댑터 — 판 1 `replay` 처럼 **판정일 = 마지막 행 날짜** 로 부른다.
    `replay(series, v1_regime, start=…)` 는 `etf_tactical.replay` 와 같은 값을 낸다(시험이 묶는다)"""
    return et.regime(list(rows), rows[-1][0])


def common_start(series: Series) -> str | None:
    """공통 구간 시작 — 계열의 13번째 월말 다음 거래일(그날 as_of 에서 12개월 수익이 처음 열린다). 없으면 None"""
    줄 = _clean(series)
    n = 0
    for i in range(len(줄) - 1):
        if _month(줄[i + 1][0]) != _month(줄[i][0]):
            n += 1
            if n == RET_MONTHS + 1:
                return 줄[i + 1][0]
    return None


def holdout_rows(series: Series, operating_first: str) -> list[tuple[str, float]]:
    """홀드아웃(문) 자료 — `HOLDOUT_FROM` ≤ 날짜 < 그 시장 운영 `index_prices` 첫 행(인자, `[확인필요]`)"""
    return [(d, c) for d, c in _clean(series) if HOLDOUT_FROM <= d < operating_first[:10]]


def lookback_from(dates: Sequence[str], as_of: str, hi: int) -> int:
    """정렬된 `dates[:hi]` 에서 as_of 달 15달 앞 1일 이상인 첫 자리 — `regime_v2` 는 최근 13개 월말만 쓰므로
    그 앞을 잘라도 값이 같다(리플레이 속도용, 시험이 같음을 묶는다)"""
    return bisect_left(dates, _month_start(_month(as_of) - RET_MONTHS - 3), 0, hi)


def _aligned(dates: list[str], series: Series | None) -> list[float | None] | None:
    """달력(`dates`)마다 그날 이하 마지막 종가(앞 값 채움). 첫 값 전은 None"""
    if series is None:
        return None
    줄 = _clean(series)
    out: list[float | None] = []
    j, 값 = 0, None
    for d in dates:
        while j < len(줄) and 줄[j][0] <= d:
            값 = 줄[j][1]
            j += 1
        out.append(값)
    return out


def _ret(a: list[float | None], i: int) -> float | None:
    return None if a[i] is None or a[i + 1] is None else a[i + 1] / a[i] - 1


def replay(
    series: Series,
    regime_fn: Callable[[Series, str], Any] | None = None,
    *,
    cost: float = COST,
    start: str | None = None,
    end: str | None = None,
    long_series: Series | None = None,
    inverse_series: Series | None = None,
) -> dict[str, Any]:
    """지수 대리 리플레이 — 판 1 `etf_tactical.replay` 와 같은 회계(바뀐 비중 1 당 `cost`, 파는 날·사는 날 하루 갈림
    `etf_tactical.step`, 비중 고정)에 **국면 함수를 인자로** 받는다(12.48 "`replay` 인자화" — 판 1 파일은
    건드리지 않음).

    날 i 의 판정: as_of = 날 i+1, 보는 행 = 날 i 까지(오늘 행 없이 — 운영 아침 배치와 같은 행), 그 비중으로
    날 i → i+1 수익을 든다. 판 1 의 '판정일 종가로 정하고 다음 날부터 든다' 와 같은 날짜 맞춤이다.
    `start`·`end` = 수익을 세는 as_of 의 첫날·끝날(포함). `start` 없으면 `common_start`.
    `long_series` = 지수 ETF 몫 시세(없으면 지수 그대로 — 테마 짝은 SPY 수정종가), `inverse_series` = 인버스 시세
    (없거나 상장 전이면 −1 × 지수 일간 수익 대리, 12.48). 보유 = 지수 ETF 몫 100% 그대로.

    기록만 지표(결과 전 고정): `switches`(파는 날·사는 날 각각), `flips_per_year`, `regime_changes`(국면 상태가 바뀐
    날 수), 상승장 날(그날 지수 ETF 몫 수익 > 0 — `[확인필요: 12.48 은 '상승장' 을 정의하지 않음]`) 가운데 현금·인버스
    였던 몫, 7일 넘는 빈 구간 수(`stale_gaps` — 리플레이가 그리지 않는 묵음, 12.48 ⑤)."""
    fn = regime_fn or regime_v2
    줄 = _clean(series)
    if start is None:
        start = common_start(줄)
    if start is None:
        return {"days": 0}
    idx = [i for i in range(len(줄) - 1) if 줄[i + 1][0] >= start[:10] and (end is None or 줄[i + 1][0] <= end[:10])]
    if not idx:
        return {"days": 0}
    날짜 = [d for d, _c in 줄]
    긴, 반대 = _aligned(날짜, long_series), _aligned(날짜, inverse_series)
    전략, 보유 = [1.0], [1.0]
    든: dict[str, float] = {}
    바꿈 = 전환 = 묵음 = 상승날 = 상승_현금 = 상승_인버스 = 0
    앞상태: str | None = None
    빠름 = fn is regime_v2  # 판정에 쓰는 것은 최근 13개 월말뿐 — 15달 앞부터 잘라 넘겨도 값이 같다(시험이 묶음)
    for i in idx:
        as_of = 줄[i + 1][0]
        앞 = lookback_from(날짜, as_of, i + 1) if 빠름 else 0
        상태 = fn(줄[앞 : i + 1], as_of).state
        if 앞상태 is not None and 상태 != 앞상태:
            전환 += 1
        앞상태 = 상태
        if (date.fromisoformat(as_of) - date.fromisoformat(줄[i][0])).days > et.STALE_DAYS:
            묵음 += 1
        목표 = et.target(상태)
        할일, 몫 = et.step(next(iter(든), None), next(iter(목표), None))
        if 할일 != "hold":
            새 = {} if 할일 == "sell" else {몫: 목표[몫]}
            변화 = sum(abs(새.get(k, 0.0) - 든.get(k, 0.0)) for k in {"long", "inverse"})
            전략[-1] *= 1 - cost * 변화
            든 = 새
            바꿈 += 1
        r = 줄[i + 1][1] / 줄[i][1] - 1
        r_long = r if 긴 is None else _ret(긴, i)
        if r_long is None:
            raise ValueError(f"{as_of}: 지수 ETF 몫 시세가 없다(상장 전) — 구간을 줄여라")
        r_inv = (_ret(반대, i) if 반대 is not None else None)
        r_inv = -r if r_inv is None else r_inv
        하루 = 든.get("long", 0.0) * r_long + 든.get("inverse", 0.0) * r_inv
        전략.append(전략[-1] * (1 + 하루))
        보유.append(보유[-1] * (1 + r_long))
        if r_long > 0:
            상승날 += 1
            상승_현금 += not 든
            상승_인버스 += "inverse" in 든
    i0, i1 = idx[0], idx[-1] + 1
    연수 = years_between(date.fromisoformat(줄[i0][0]), date.fromisoformat(줄[i1][0]))
    곡선 = list(zip(날짜[i0 : i1 + 1], 전략, strict=True))
    return {
        "days": len(전략) - 1, "from": 줄[i0][0], "to": 줄[i1][0], "switches": 바꿈,
        "flips_per_year": 바꿈 / 연수 if 연수 > 0 else None, "regime_changes": 전환,
        "twr_pct": (전략[-1] - 1) * 100, "hold_pct": (보유[-1] - 1) * 100,
        "excess_pct": (전략[-1] - 보유[-1]) * 100,
        "mdd_pct": mdd(전략) * 100, "hold_mdd_pct": mdd(보유) * 100,
        "up_days": 상승날,
        "up_cash_share": 상승_현금 / 상승날 if 상승날 else None,
        "up_inverse_share": 상승_인버스 / 상승날 if 상승날 else None,
        "up_not_long_share": (상승_현금 + 상승_인버스) / 상승날 if 상승날 else None,
        "stale_gaps": 묵음,
        "equity": 곡선, "hold_equity": list(zip(날짜[i0 : i1 + 1], 보유, strict=True)),
    }  # fmt: skip


def mdd(values: Sequence[float]) -> float:
    """고점 대비 최대 하락률(음수) — `etf_tactical._mdd` 와 같은 식"""
    고점, 최악 = -math.inf, 0.0
    for v in values:
        고점 = max(고점, v)
        최악 = min(최악, v / 고점 - 1)
    return 최악


def _same_window(a: dict[str, Any], b: dict[str, Any]) -> None:
    for k in ("from", "to", "days"):
        if a.get(k) != b.get(k):
            raise ValueError(f"공통 구간이 아니다 — {k}: {a.get(k)} ≠ {b.get(k)} (12.48 '공통 구간')")


def holdout_gate(
    v2: dict[str, Any], v1: dict[str, Any], account: str, operating_first: str | None = None,
) -> dict[str, Any]:
    """홀드아웃 문(12.48, 시장마다 셋 모두) — `replay` 결과 둘(같은 계열·같은 `start`·`end`).

    (a) 판 2 시간가중 > 판 1 시간가중 (같으면 못 넘음)
    (b) 판 2 연 뒤집힘 < 판 1 연 뒤집힘 (`switches` ÷ 연수, 같으면 못 넘음)
    (c) MDD_판2 ≥ 0.7 × MDD_보유 — MDD 는 음수라 |MDD_판2| ≤ 0.7 × |MDD_보유| (경계 같음은 넘음)
    구간이 `HOLDOUT_FROM` 전에서 시작하거나 운영 첫 행(`operating_first`) 이후에 닿으면 문이 아니다(ValueError)."""
    _same_window(v2, v1)
    if not v2.get("days"):
        raise ValueError("리플레이 날이 0 — 판정할 수 없다")
    if v2["from"] < HOLDOUT_FROM or (operating_first is not None and v2["to"] >= operating_first[:10]):
        raise ValueError(f"홀드아웃 구간 밖: {v2['from']}~{v2['to']}")
    a = v2["twr_pct"] > v1["twr_pct"]
    b = v2["flips_per_year"] is not None and v1["flips_per_year"] is not None and (
        v2["flips_per_year"] < v1["flips_per_year"])  # fmt: skip
    c = v2["mdd_pct"] >= MDD_RATIO * v2["hold_mdd_pct"]
    return {
        "account": account, "pass": a and b and c, "a": a, "b": b, "c": c,
        "literature_sample": account in LITERATURE_SAMPLE,
        # 기록만 — 결과 전 고정한 지표(통과 근거가 아니다)
        "excess_vs_hold_pct": v2["twr_pct"] - v2["hold_pct"], "regime_changes": v2["regime_changes"],
        "up_not_long_share": v2["up_not_long_share"], "stale_gaps": v2["stale_gaps"],
    }  # fmt: skip


def account_judgement(
    n_eval: int, twr_pct: float, hold_twr_pct: float, mdd_pct: float, hold_mdd_pct: float,
    regime_changes: int, *, degenerate_at_126: bool = False,
) -> str:  # fmt: skip
    """계좌 판정(desk.PREREG (X,2)·(Y,2) — 판 2 첫날부터 센 평가 수).

    126번째에서 한 번: MDD_판2 ≥ 0.7 × MDD_보유 그리고 시간가중 ≥ 보유 − 5%p 면 '통과', 아니면 '실패'.
    그때까지 국면 전환이 0번이면 '판정 불가(퇴화)' — 252번째에서 같은 기준으로 한 번만 다시 보고
    (`degenerate_at_126=True`), 그때도 0번이면 '판정 불가' 로 끝낸다."""
    차례 = REJUDGE_EVAL if degenerate_at_126 else JUDGE_EVAL
    if n_eval < 차례:
        return "대기"
    if regime_changes == 0:
        return "판정 불가" if degenerate_at_126 else "판정 불가(퇴화) — 252번째에 한 번 더"
    ok = mdd_pct >= MDD_RATIO * hold_mdd_pct and twr_pct >= hold_twr_pct - RETURN_SLACK_PP
    return "통과" if ok else "실패"
