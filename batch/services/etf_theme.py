"""업종 ETF 그림자 장부 `etf_theme_v1` — 판 2 상승 달에만 SPDR 업종 상위 3 (docs/factors.md 12.48 (나) 사전 등록).

**꺼진 채다.** 계좌가 아니라 Y 옆 그림자 장부이고(사용자 지시 "계좌는 두개"), 운영 경로가 이 파일을 부르지 않는다.

    국면 = `etf_tactical_v2.regime_v2`(Y: ^GSPC)와 같은 as_of 정의
    상승 월말: 고정 목록 9종(+XLRE·XLC 는 각자 이어진 13개 월말이 찬 월말부터)의 12개월 총수익(수정종가,
              `etf_tactical_v2.ret12`) 상위 3 을 98%/3 씩. 뽑힌 업종의 12개월 수익 < 0 이면
              그 몫은 SPY (Antonacci, 기준 0)
    하락·모름: trading-2 Y 와 같다 (12개월 수익 음이면 SH 50%, 아니면 현금, 모름은 현금)
    몫 = 순위 자리 셋. 각 몫의 목표 자산이 든 것과 다를 때만 그 몫을 갈아탄다. 판 날은 사지 않는다(하루 띄우기)
    월말 순위에 쓰는 ETF 가 수정주가 재수집 대기열에 있으면 그달은 다시 맞추지 않는다(든 것 유지)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from batch.services import etf
from batch.services import etf_tactical as et
from batch.services import etf_tactical_v2 as v2
from batch.services.metrics import years_between

RULE = "etf_theme_v1"
#: 고정 목록(12.48) — SPDR 업종 9종. 바꾸면 새 판이고 시도 +1
SECTORS = ("XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY")
#: 늦게 상장한 둘 — 각자 이어진 13개 월말 이력이 찬 월말부터 넣는다(시점 기준 구성)
LATE_SECTORS = ("XLRE", "XLC")
UNIVERSE = SECTORS + LATE_SECTORS
FILL = et.ACCOUNTS["Y"]["long"]  # SPY — 음수 몫·빈 몫 채움
INVERSE = et.ACCOUNTS["Y"]["inverse"]  # SH
#: 상위 몇 개 — selection-2 가 '근거 없는 첫 값' 이라 적은 k (12.48)
TOP_K = 3
#: 그림자 탈락 점검 — (테마 − trading-2 Y) 누적 초과가 이 %p 미만이면 접는다. 검증 B 의 근거 없는 첫 값 `[확인필요]`
HARM_PP = -3.0
#: 미국 '합성'(스왑 복제) 이름 표기 — etf.py 에 미국 합성 거르개가 없어 여기 둔다 `[확인필요: 실제 이름 대조]`
US_SYNTHETIC_PATTERN = re.compile(r"synthetic|swap", re.IGNORECASE)
#: 국내판(기록만)에 더하는 표기 — 12.48 은 `etf.KR_NAME_EXCLUSIONS`·`KR_MULTIPLE_PATTERN` + '선물'·'커버드콜'.
#: 커버드콜은 이미 etf.py 에 있다. 정의처를 etf.py 로 옮기는 것은 국내판 등록 커밋 몫 `[확인필요]`
KR_EXTRA_MARKERS = ("선물",)

Series = Sequence[tuple[str, float | None]]
Legs = dict[Any, tuple[str, float]]  # 몫 열쇠(순위 자리 0·1·2 또는 "inv") → (자산, 비중)


def us_excluded(name: str | None = None, category: str | None = None) -> str | None:
    """레버리지·인버스·합성·옵션 전략이면 사유, 아니면 None. SH 는 후보가 아니라 등록된 인버스 다리라 여기 안 온다"""
    if (category and category.startswith(etf.TRADING_CATEGORY_PREFIX)) or (name and etf.name_looks_leveraged(name)):
        return "레버리지·인버스"
    if name and US_SYNTHETIC_PATTERN.search(name):
        return "합성"
    if name and etf.name_looks_option(name):
        return "옵션 전략"
    return None


def kr_excluded(name: str) -> str | None:
    """국내판 이름 거르개(기록만, 시도 0) — etf.py 의 표기 + '선물'"""
    for marker, reason in etf.KR_NAME_EXCLUSIONS:
        if marker in name:
            return reason
    if etf.name_looks_leveraged(name) or etf.KR_MULTIPLE_PATTERN.search(name):
        return etf.KR_NAME_EXCLUSIONS[0][1]
    for marker in KR_EXTRA_MARKERS:
        if marker in name:
            return f"{marker} — 현물 지수를 그대로 따르지 않는다"
    return None


def rank(
    sector_series: Mapping[str, Series], as_of: str, *, universe: Iterable[str] = UNIVERSE,
    meta: Mapping[str, Mapping[str, str | None]] | None = None,
) -> list[tuple[str, float]]:  # fmt: skip
    """as_of 판정의 순위 — [(티커, 12개월 수익)] 수익 큰 것부터, **동률은 티커 글자순**
    (`[확인필요: 티커 순으로 등록 커밋에 고정]` 을 여기서 고정). 이어진 13개 월말이 없는 ETF(상장 전·빈 달)와
    `meta`(티커 → name·category)로 걸리는 ETF 는 넣지 않는다."""
    out = []
    for t in universe:
        if t not in sector_series:
            continue
        m = (meta or {}).get(t) or {}
        if us_excluded(m.get("name"), m.get("category")):
            continue
        r = v2.ret12(sector_series[t], as_of)
        if r is not None:
            out.append((t, r))
    return sorted(out, key=lambda x: (-x[1], x[0]))


def targets(state: str, ranked: Sequence[tuple[str, float]]) -> Legs:
    """국면·순위 → 목표 몫. 상승: 순위 자리 셋, 각 98%/3 (12개월 수익 < 0 이면 SPY, 후보가 3개 못 되면 남은 자리도 SPY
    `[확인필요: 12.48 이 닫지 않은 빈 경우 — 짝(trading-2 Y = SPY)과 같아지는 쪽]`). 하락: SH 50%. 그 밖: 현금"""
    if state == v2.UP:
        w = et.LONG_WEIGHT / TOP_K
        자리 = [(t if r >= 0 else FILL) for t, r in ranked[:TOP_K]]
        자리 += [FILL] * (TOP_K - len(자리))
        return {k: (a, w) for k, a in enumerate(자리)}
    if state == v2.DOWN:
        return {"inv": (INVERSE, et.INVERSE_WEIGHT)}
    return {}


def month_plan(
    regime: v2.RegimeV2, sector_series: Mapping[str, Series], held: Legs, *,
    pending: Iterable[str] = (), meta: Mapping[str, Mapping[str, str | None]] | None = None,
) -> tuple[Legs, bool, list[tuple[str, float]]]:  # fmt: skip
    """그달 목표 — (목표 몫, 건너뜀 여부, 순위). **상승 달에** 순위에 쓰는 ETF(`rank` 에 든 것) 하나라도
    수정주가 재수집 대기열(`pending` — `adjust_refresh_queue` 의 done_at 없는 티커)에 있으면 다시 맞추지 않고 든 것을
    그대로 둔다. 하락·모름 달의 국면 빠져나가기는 순위를 쓰지 않으므로 건너뛰지 않는다(모르면 들지 않는다)."""
    ranked = rank(sector_series, regime.as_of, meta=meta) if regime.state == v2.UP else []
    if regime.state == v2.UP and set(pending) & {t for t, _r in ranked}:
        return dict(held), True, ranked
    return targets(regime.state, ranked), False, ranked


def trade(held: Legs, want: Legs) -> tuple[Legs, float, int]:
    """하루 매매 — (새 몫, 바뀐 비중 합, 매매 수). 몫마다 자산이 다르면 판다. **판 날은 사지 않는다** — 그날 하나라도
    팔면 사는 것은 다음 실행(판 돈이 장부에 든 뒤, `etf_tactical.step` 과 같은 하루 띄우기)"""
    팔 = [k for k, (a, _w) in held.items() if k not in want or want[k][0] != a]
    if 팔:
        새 = {k: v for k, v in held.items() if k not in 팔}
        return 새, sum(held[k][1] for k in 팔), len(팔)
    살 = [k for k in want if k not in held]
    새 = {**held, **{k: want[k] for k in 살}}
    return 새, sum(want[k][1] for k in 살), len(살)


def replay(
    index_series: Series, sector_series: Mapping[str, Series], spy_series: Series, *,
    sh_series: Series | None = None, cost: float = v2.COST, start: str | None = None, end: str | None = None,
    pending_by_month: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, Any]:  # fmt: skip
    """테마 리플레이 — `etf_tactical_v2.replay` 와 같은 날짜 맞춤·회계(비중 고정, 바뀐 비중 1 당 `cost`).
    달력 = 지수(^GSPC) 행. 업종·SPY 는 수정종가, SH 는 없거나 상장 전이면 −1 × 지수 일간 수익(짝과 대칭).
    `pending_by_month` = {'YYYY-MM': 대기열 티커} — 리플레이는 보통 비운다(한 번에 받은 일관된 수정종가 — 이름 붙은
    차이). 그달 계획은 그달 첫 as_of 에 정한다. 달 중간 묵음 날은 모름 → 현금, 풀리면 그달 계획(순위가 같다).
    건너뛴 달에 묵음으로 현금이 되면 '든 것 유지' 가 현금이 된다 `[참고]`."""
    줄 = v2._clean(index_series)
    if start is None:
        start = v2.common_start(줄)
    if start is None:
        return {"days": 0}
    idx = [i for i in range(len(줄) - 1) if 줄[i + 1][0] >= start[:10] and (end is None or 줄[i + 1][0] <= end[:10])]
    if not idx:
        return {"days": 0}
    날짜 = [d for d, _c in 줄]
    시세 = {t: v2._aligned(날짜, s) for t, s in sector_series.items()}
    시세[FILL] = v2._aligned(날짜, spy_series)
    반대 = v2._aligned(날짜, sh_series)
    값 = [1.0]
    든: Legs = {}
    매매 = 0
    이번달: str | None = None
    계획: Legs = {}
    계획국면: str | None = None
    상승달: set[str] = set()
    건너뜀: list[str] = []
    for i in idx:
        as_of = 줄[i + 1][0]
        국면 = v2.regime_v2(줄[v2.lookback_from(날짜, as_of, i + 1) : i + 1], as_of)
        if as_of[:7] != 이번달 or 국면.state != 계획국면:
            # 그달 첫 as_of 에 정하고, 달 안에서는 국면이 바뀔 때(묵음 → 모름 → 현금, 풀리면 그달 판정)만 다시 낸다.
            # 순위는 끝난 월말로만 정해지므로 달 안에서 다시 내도 같다
            새달 = as_of[:7] != 이번달
            이번달 = as_of[:7]
            pend = (pending_by_month or {}).get(이번달, ())
            계획, skip, _r = month_plan(국면, sector_series, 든, pending=pend)
            계획국면 = 국면.state
            if 국면.state == v2.UP:
                상승달.add(이번달)
            if skip and 새달:
                건너뜀.append(이번달)
        든, 변화, n = trade(든, 계획)
        if n:
            값[-1] *= 1 - cost * 변화
            매매 += n
        r = 줄[i + 1][1] / 줄[i][1] - 1
        하루 = 0.0
        for a, w in 든.values():
            if a == INVERSE:
                ri = v2._ret(반대, i) if 반대 is not None else None
                하루 += w * (-r if ri is None else ri)
                continue
            ra = v2._ret(시세[a], i)
            if ra is None:
                raise ValueError(f"{as_of}: {a} 시세가 없다")
            하루 += w * ra
        값.append(값[-1] * (1 + 하루))
    i0, i1 = idx[0], idx[-1] + 1
    연수 = years_between(date.fromisoformat(줄[i0][0]), date.fromisoformat(줄[i1][0]))
    return {
        "days": len(값) - 1, "from": 줄[i0][0], "to": 줄[i1][0], "trades": 매매,
        "trades_per_year": 매매 / 연수 if 연수 > 0 else None,
        "twr_pct": (값[-1] - 1) * 100, "mdd_pct": v2.mdd(값) * 100,
        "up_months": len(상승달), "skipped_months": 건너뜀,
        "equity": list(zip(날짜[i0 : i1 + 1], 값, strict=True)),
    }  # fmt: skip


def halves(equity: Sequence[tuple[str, float]]) -> tuple[float, float]:
    """기간을 날 수로 이등분한 앞 반·뒤 반 수익(비율). n 날이면 가르는 점은 n // 2 번째 날 끝(홀수면 뒤 반이 하루 더)"""
    v = [x for _d, x in equity]
    n = len(v) - 1
    if n < 2:
        raise ValueError("이등분할 날이 모자란다")
    m = n // 2
    return v[m] / v[0] - 1, v[n] / v[m] - 1


def holdout_gate(theme: dict[str, Any], pair: dict[str, Any], pair_gate_passed: bool) -> dict[str, Any]:
    """홀드아웃 문 ①(12.48, 셋 모두) — `theme` = 이 파일 `replay`, `pair` = trading-2 Y `etf_tactical_v2.replay`
    (`long_series` = SPY 수정종가, 같은 `start`·`end`). 보유 MDD 는 짝의 `hold_mdd_pct`(SPY 수정종가 보유).

    (i) 테마 시간가중 > 짝, (ii) 앞 반·뒤 반 **각각** >, (iii) MDD_테마 ≥ 0.7 × MDD_SPY보유(음수 규약).
    짝이 자기 문(`etf_tactical_v2.holdout_gate`)을 못 넘으면 열지 않는다."""
    v2._same_window(theme, pair)
    if [d for d, _x in theme["equity"]] != [d for d, _x in pair["equity"]]:
        raise ValueError("테마와 짝의 날짜가 다르다")
    i = theme["twr_pct"] > pair["twr_pct"]
    (ta, tb), (pa, pb) = halves(theme["equity"]), halves(pair["equity"])
    ii = ta > pa and tb > pb
    iii = theme["mdd_pct"] >= v2.MDD_RATIO * pair["hold_mdd_pct"]
    return {"pass": bool(pair_gate_passed and i and ii and iii), "pair_gate": pair_gate_passed,
            "i": i, "ii": ii, "iii": iii, "halves": {"theme": (ta, tb), "pair": (pa, pb)}}  # fmt: skip


def shadow_check(
    theme_values: Sequence[float], pair_values: Sequence[float], spy_values: Sequence[float], up_months: int,
) -> dict[str, Any]:  # fmt: skip
    """그림자 탈락 점검 ②(Y 판 2 의 126번째 평가 시점, 통과 판정 아님) — 세 장부의 날별 평가액(같은 날들, 첫 값이 시작).

    상승 달이 0 이면 '판정 불가(탈락 아님)'. (테마 − 짝) 누적 초과 < −3%p(`HARM_PP`) 이거나
    |MDD_테마| > 0.7 × |MDD_SPY보유| 이면 '접음', 아니면 '유지(통과 판정 아님)'. 경계 같음은 접지 않는다."""
    if not (len(theme_values) == len(pair_values) == len(spy_values)) or len(theme_values) < 2:
        raise ValueError("세 장부의 날 수가 같아야 한다")
    초과 = (theme_values[-1] / theme_values[0] - pair_values[-1] / pair_values[0]) * 100
    m_t = v2.mdd([x / theme_values[0] for x in theme_values]) * 100
    m_s = v2.mdd([x / spy_values[0] for x in spy_values]) * 100
    out = {"excess_pp": 초과, "mdd_pct": m_t, "spy_mdd_pct": m_s, "n_up_months": up_months}
    if up_months == 0:
        return {**out, "verdict": "판정 불가(탈락 아님)"}
    접음 = 초과 < HARM_PP or m_t < v2.MDD_RATIO * m_s
    return {**out, "verdict": "접음" if 접음 else "유지(통과 판정 아님)"}
