"""거장 부서 — 유명 투자가 책의 숫자 규칙을 판단식으로 (docs/company.md 12장, docs/infra.md 25.2261).

2026-11-23 사용자 지시("세계 유명한 투자가 … 투자기법을 녹인 모의투자계좌",
"계좌 이름이나 에이전트(부서명)에 해당 이름도 같이").
부서는 0장 원칙 그대로 **판단식**이다 — 유료 모델·재현·근거표. 정성 판단(해자·경영진·이야기)은 숫자 근사로만 옮긴다.
**정의처는 docs/company.md 12장**이다 — 문턱·근사를 바꾸면 거장 판(ZM·ZN·ZL·Z3·Z4)을 함께 올린다(12.8).
이 파일은 논리 지문(`tests/test_dept_logic_frozen_1488.py`)이 덮는다.

입력은 `Dossier.masters`(부르는 쪽 `jobs/company.master_facts` 가 기준일까지 알 수 있던 값만 싣는다):

- ``annual``: [{fy, ni, eq, ta, ca, cl, ncl, oi, ocf, cur}] 사업연도 오름차순
  (그때 고른 연결·별도 한 기준, 같은 사업연도는 가장 늦은 접수)
- ``raw``: 그날 점수의 팩터 원값 {ep, bp, dy, cap, roe, debt_ratio, profit_stability, current_ratio, high52, mom12_1}
- ``div``: {사업연도: 배당 있음(bool)}
- ``beta``·``beta_as_of``, ``financial``·``utility``(업종, 모르면 None), ``price_currency``(시총 통화)
"""

from __future__ import annotations

from statistics import median
from typing import Any

from batch.services.company import Dossier, Opinion, _criterion, _없음, percentile_ranks

#: 부서 키 → 화면 이름 (사용자 지시 — 부서 이름에 투자가 이름)
LABELS: dict[str, str] = {
    "buffett": "버핏부(가치·해자)",
    "lynch": "린치부(PEG·성장)",
    "lynch_check": "린치부(재무 점검)",
    "graham": "그레이엄부(방어적 투자자)",
    "graham_price": "그레이엄부(가격·안전마진)",
    "graham_fin": "그레이엄부(재무·이력)",
    "greenblatt": "그린블랫부(마법공식)",
    "oneil_earn": "오닐부(실적 A)",
    "oneil_price": "오닐부(주가 N·L)",
}
DEPTS: tuple[str, ...] = tuple(LABELS)

# ── 책의 문턱 (company.md 12.3 — 맞춘 값 없음) ─────────────────────────────────────────────
BUFFETT_ROE = 0.15  # 버핏 1987 주주서한의 "ROE 15%" 관례 [확인필요: 원문 쪽수]
BUFFETT_MIN_YEARS = 3  # 최근 5 사업연도 가운데 관측 ≥ 3 이어야 ① 판정
BUFFETT_BETA = 1.0
LYNCH_FOR = 2.0  # (g + 배당) ÷ PER ≥ 2 매우 좋음, < 1 나쁨 (Lynch 1989)
LYNCH_AGAINST = 1.0
LYNCH_CONVICTION = 0.5
GRAHAM_PER = 15.0
GRAHAM_PER_PBR = 22.5
GRAHAM_CURRENT = 2.0
GRAHAM_YEARS = 5
ONEIL_CAGR = 0.25
ONEIL_ROE = 0.17
ONEIL_HIGH52 = 0.85  # 신고가 15% 안 [확인필요: 판본별 값]
ONEIL_RS = 80.0
CHECK_FOR = 0.7
CHECK_AGAINST = 0.3
PCT_FOR = 70.0
PCT_AGAINST = 30.0
LYNCH_CHECK_FOR = 50.0  # 부채 백분위 ≤ 50 이면 찬성 쪽
LYNCH_CHECK_AGAINST = 70.0


def _f(x: Any) -> float | None:
    return float(x) if isinstance(x, int | float) and not isinstance(x, bool) else None


def _m(d: Dossier) -> dict[str, Any]:
    return d.masters or {}


def _연간(d: Dossier) -> list[dict[str, Any]]:
    return list(_m(d).get("annual") or [])


def _최근(d: Dossier, n: int) -> list[dict[str, Any]]:
    """가장 늦은 사업연도부터 n 개 사업연도 창(빈 해는 빠진 채로) — 오름차순"""
    연간 = _연간(d)
    if not 연간:
        return []
    끝 = int(연간[-1]["fy"])
    return [r for r in 연간 if 끝 - n < int(r["fy"]) <= 끝]


def _해(d: Dossier, fy: int) -> dict[str, Any] | None:
    return next((r for r in _연간(d) if int(r["fy"]) == fy), None)


def _roe(r: dict[str, Any]) -> float | None:
    ni, eq = _f(r.get("ni")), _f(r.get("eq"))
    return ni / eq if ni is not None and eq is not None and eq > 0 else None


def _cagr3(d: Dossier) -> float | None:
    """순이익 3년 CAGR — 최근 사업연도와 3년 전이 모두 있고 그 사이 네 해 순이익이 모두 > 0 일 때만"""
    연간 = _연간(d)
    if not 연간:
        return None
    끝 = int(연간[-1]["fy"])
    네해 = [_해(d, 끝 - k) for k in range(3, -1, -1)]
    if any(r is None or _f(r.get("ni")) is None or float(r["ni"]) <= 0 for r in 네해):
        return None
    return (float(네해[-1]["ni"]) / float(네해[0]["ni"])) ** (1 / 3) - 1  # type: ignore[index]


def _통화맞음(d: Dossier) -> bool:
    """시총과 재무를 섞는 항목은 둘의 통화가 같을 때만 (company.md 12.1)"""
    연간 = _연간(d)
    cur = (연간[-1].get("cur") if 연간 else None) or None
    pc = _m(d).get("price_currency")
    return bool(cur) and bool(pc) and str(cur).upper() == str(pc).upper()


def _raw(d: Dossier, k: str) -> float | None:
    return _f((_m(d).get("raw") or {}).get(k))


def _per_3y(d: Dossier) -> float | None:
    """3년 평균 순이익 기준 PER = 시총 ÷ 평균(최근 3 사업연도 순이익). 셋 다 있고 평균 > 0, 통화가 같을 때만"""
    if not _통화맞음(d):
        return None
    cap = _raw(d, "cap")
    셋 = _최근(d, 3)
    이익 = [_f(r.get("ni")) for r in 셋]
    if cap is None or cap <= 0 or len(셋) < 3 or any(x is None for x in 이익):
        return None
    평균 = sum(이익) / 3  # type: ignore[arg-type]
    return cap / 평균 if 평균 > 0 else None


def _체크(항목: list[tuple[str, bool | None, str, str]]) -> tuple[float | None, int]:
    """[(이름, 충족(None=판정 불가), 값 글, 문턱 글)] → (s, 판정 가능 수)"""
    가능 = [ok for _n, ok, _v, _t in 항목 if ok is not None]
    return (sum(가능) / len(가능) if 가능 else None), len(가능)


def _체크_의견(dept: str, d: Dossier, 항목: list[tuple[str, bool | None, str, str]], 출처: str) -> Opinion:
    """체크리스트형 (12.2): 판정 가능 < min(3, 항목 수) 이면 자료 없음, s ≥ 0.7 찬성 · ≤ 0.3 반대"""
    s, n = _체크(항목)
    근거 = [_criterion(이름, 값, 문턱, 출처, d.as_of) for 이름, _ok, 값, 문턱 in 항목]
    if s is None or n < min(3, len(항목)):
        return Opinion(dept, 0, 0.0, 근거, note=f"자료 없음 — 판정 가능 {n}/{len(항목)}")
    입장 = 1 if s >= CHECK_FOR else -1 if s <= CHECK_AGAINST else 0
    return Opinion(dept, 입장, round(abs(s - 0.5) / 0.5, 6), 근거)


def _반쪽_의견(dept: str, d: Dossier, 항목: list[tuple[str, bool | None, str, str]], 출처: str) -> Opinion:
    """반쪽형 (12.2): 모두 판정 가능·모두 충족 = 찬성(확신 1), 판정 가능 s ≤ 0.3 반대, 판정 가능 0 자료 없음"""
    s, n = _체크(항목)
    근거 = [_criterion(이름, 값, 문턱, 출처, d.as_of) for 이름, _ok, 값, 문턱 in 항목]
    if s is None:
        return Opinion(dept, 0, 0.0, 근거, note=f"자료 없음 — 판정 가능 0/{len(항목)}")
    if n == len(항목) and s == 1.0:
        return Opinion(dept, 1, 1.0, 근거)
    if s <= CHECK_AGAINST:
        return Opinion(dept, -1, round(abs(s - 0.5) / 0.5, 6), 근거)
    return Opinion(dept, 0, 0.0, 근거)


def _p(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def _x(x: float | None, digits: int = 2) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


# ── 부서 ──────────────────────────────────────────────────────────────────────────


def buffett(dossiers: list[Dossier], population: list[Dossier]) -> dict[int, Opinion]:
    """버핏부(가치·해자) 체크리스트 6 (12.3)"""
    부채 = [v for v in (_raw(p, "debt_ratio") for p in population) if v is not None]
    ep들 = [v for v in (_raw(p, "ep") for p in population) if v is not None]
    부채중 = median(부채) if 부채 else None
    ep중 = median(ep들) if ep들 else None
    out: dict[int, Opinion] = {}
    for d in dossiers:
        다섯 = _최근(d, 5)
        roe들 = [x for x in (_roe(r) for r in 다섯) if x is not None]
        b1 = None if len(roe들) < BUFFETT_MIN_YEARS else all(x >= BUFFETT_ROE for x in roe들)
        ps = _raw(d, "profit_stability")
        b2 = None if ps is None else ps >= 1.0
        dr = _raw(d, "debt_ratio")
        b3 = None if dr is None or 부채중 is None else dr <= 부채중
        끝 = _연간(d)[-1] if _연간(d) else {}
        ocf, ni = _f(끝.get("ocf")), _f(끝.get("ni"))
        b4 = None if ocf is None or ni is None else ocf >= ni
        beta = _f(_m(d).get("beta"))
        b5 = None if beta is None else beta <= BUFFETT_BETA
        ep = _raw(d, "ep")
        b6 = None if ep is None or ep중 is None else ep >= ep중
        out[d.stock_id] = _체크_의견("buffett", d, [
            ("ROE 지속", b1, f"{len(roe들)}해 최저 {_p(min(roe들) if roe들 else None)}",
             f"관측된 해(5 중 ≥ {BUFFETT_MIN_YEARS}) 모두 ≥ {BUFFETT_ROE:.0%}"),
            ("이익 안정성", b2, _x(ps), "= 1"),
            ("부채비율", b3, _x(dr), f"≤ 신호 종목 중앙값 {_x(부채중)}"),
            ("영업현금흐름 대 순이익", b4, f"{_x(ocf, 0)} 대 {_x(ni, 0)}", "영업현금흐름 ≥ 순이익(오너 어닝 근사)"),
            ("베타", b5, _x(beta), f"≤ {BUFFETT_BETA:g}"),
            ("이익수익률", b6, _x(ep, 4), f"≥ 신호 종목 중앙값 {_x(ep중, 4)}"),
        ], "financial_snapshots·factors·performance_metrics")  # fmt: skip
    return out


def lynch_ratio(d: Dossier) -> float | None:
    """R = (g + 배당수익률%) ÷ PER (12.3)"""
    ep, g = _raw(d, "ep"), _cagr3(d)
    if ep is None or ep <= 0 or g is None:
        return None
    dy = _raw(d, "dy") or 0.0
    return (g * 100 + dy * 100) / (1 / ep)


def lynch(dossiers: list[Dossier], population: list[Dossier]) -> dict[int, Opinion]:
    """린치부(PEG·성장) — 확신 0.5 고정"""
    out: dict[int, Opinion] = {}
    for d in dossiers:
        r = lynch_ratio(d)
        if r is None:
            out[d.stock_id] = _없음("lynch", "순이익 3년 성장(네 해 흑자) 또는 이익수익률 없음")
            continue
        입장 = 1 if r >= LYNCH_FOR else -1 if r < LYNCH_AGAINST else 0
        g = _cagr3(d)
        범주 = "고성장" if g is not None and g >= 0.20 else "저성장" if g is not None and g < 0.05 else "중성장"
        out[d.stock_id] = Opinion("lynch", 입장, LYNCH_CONVICTION, [_criterion(
            "린치 비율", f"{r:.2f} (성장 {_p(g)} · 배당 {_p(_raw(d, 'dy'))} · PER {_x(1 / _raw(d, 'ep'), 1)}, {범주})",  # type: ignore[operator]
            f"찬성 ≥ {LYNCH_FOR:g} · 반대 < {LYNCH_AGAINST:g}", "financial_snapshots·factors", d.as_of)])  # fmt: skip
    return out


def lynch_check(dossiers: list[Dossier], population: list[Dossier]) -> dict[int, Opinion]:
    """린치부(재무 점검) — 부채 백분위·영업현금흐름"""
    pct = percentile_ranks({p.stock_id: _raw(p, "debt_ratio") for p in population})
    out: dict[int, Opinion] = {}
    for d in dossiers:
        if d.stock_id not in pct:
            out[d.stock_id] = _없음("lynch_check", "부채비율 없음")
            continue
        p = pct[d.stock_id]
        끝 = _연간(d)[-1] if _연간(d) else {}
        ocf, ni, eq = _f(끝.get("ocf")), _f(끝.get("ni")), _f(끝.get("eq"))
        나쁨 = (eq is not None and eq <= 0) or p >= LYNCH_CHECK_AGAINST or (ocf is not None and ocf < 0)
        좋음 = p <= LYNCH_CHECK_FOR and (ocf is None or ni is None or ocf >= ni)
        입장 = -1 if 나쁨 else 1 if 좋음 else 0
        out[d.stock_id] = Opinion("lynch_check", 입장, round(abs(p - 50) / 50, 6), [
            _criterion("부채비율 백분위", f"{p:.0f} ({_x(_raw(d, 'debt_ratio'))})",
                       f"찬성 ≤ {LYNCH_CHECK_FOR:g} · 반대 ≥ {LYNCH_CHECK_AGAINST:g}", "factors", d.as_of),
            _criterion("영업현금흐름 대 순이익", f"{_x(ocf, 0)} 대 {_x(ni, 0)}",
                       "찬성은 영업현금흐름 ≥ 순이익(있으면) · 반대는 영업현금흐름 < 0 또는 자본잠식",
                       "financial_snapshots", d.as_of),
        ])  # fmt: skip
    return out


def graham_items(d: Dossier) -> list[tuple[str, bool | None, str, str]]:
    """그레이엄 일곱 항목 g1~g7 (12.3) — 앞 둘이 가격, 뒤 다섯이 재무·이력"""
    per = _per_3y(d)
    bp = _raw(d, "bp")
    pbr = 1 / bp if bp is not None and bp > 0 else None
    끝 = _연간(d)[-1] if _연간(d) else {}
    ca, cl, ncl = _f(끝.get("ca")), _f(끝.get("cl")), _f(끝.get("ncl"))
    cr = _raw(d, "current_ratio")
    다섯 = _최근(d, GRAHAM_YEARS)
    모두 = len(다섯) == GRAHAM_YEARS
    흑자 = None if not 모두 or any(_f(r.get("ni")) is None for r in 다섯) else all(float(r["ni"]) > 0 for r in 다섯)
    해들 = [int(r["fy"]) for r in 다섯]
    배당표 = _m(d).get("div") or {}
    배당 = None if not 모두 or any(str(y) not in 배당표 and y not in 배당표 for y in 해들) else all(
        bool(배당표.get(y, 배당표.get(str(y)))) for y in 해들)
    끝해 = int(끝["fy"]) if 끝 else None
    옛 = _해(d, 끝해 - 3) if 끝해 is not None else None
    ni, ni3 = _f(끝.get("ni")), (_f(옛.get("ni")) if 옛 else None)
    return [
        ("PER(3년 평균 이익)", None if per is None else per <= GRAHAM_PER, _x(per, 1), f"≤ {GRAHAM_PER:g}"),
        ("PER × PBR", None if per is None or pbr is None else per * pbr <= GRAHAM_PER_PBR,
         _x(None if per is None or pbr is None else per * pbr, 1), f"≤ {GRAHAM_PER_PBR:g}"),
        ("유동비율", None if cr is None else cr >= GRAHAM_CURRENT, _x(cr), f"≥ {GRAHAM_CURRENT:g}"),
        ("비유동부채 대 순유동자산", None if None in (ca, cl, ncl) else ncl <= ca - cl,  # type: ignore[operator]
         f"{_x(ncl, 0)} 대 {_x(None if ca is None or cl is None else ca - cl, 0)}", "비유동부채 ≤ 유동자산 − 유동부채"),
        ("흑자 이력", 흑자, f"{len(다섯)}해", f"최근 {GRAHAM_YEARS} 사업연도 모두 있고 모두 순이익 > 0"),
        ("배당 이력", 배당, f"{len(다섯)}해", f"최근 {GRAHAM_YEARS} 사업연도 모두 배당"),
        ("이익 증가", None if ni is None or ni3 is None else ni > ni3, f"{_x(ni, 0)} 대 3년 전 {_x(ni3, 0)}",
         "최근 순이익 > 3년 전"),
    ]  # fmt: skip


def graham(dossiers: list[Dossier], population: list[Dossier]) -> dict[int, Opinion]:
    return {d.stock_id: _체크_의견("graham", d, graham_items(d), "financial_snapshots·factors·stock_dividends")
            for d in dossiers}  # fmt: skip


def graham_price(dossiers: list[Dossier], population: list[Dossier]) -> dict[int, Opinion]:
    return {d.stock_id: _반쪽_의견("graham_price", d, graham_items(d)[:2], "financial_snapshots·factors")
            for d in dossiers}  # fmt: skip


def graham_fin(dossiers: list[Dossier], population: list[Dossier]) -> dict[int, Opinion]:
    return {d.stock_id: _반쪽_의견("graham_fin", d, graham_items(d)[2:], "financial_snapshots·factors·stock_dividends")
            for d in dossiers}  # fmt: skip


def greenblatt_parts(d: Dossier) -> tuple[float | None, float | None]:
    """(EY 근사, ROC 근사) — 금융·유틸리티·영업이익 ≤ 0·분모 ≤ 0·통화 다름은 (None, None)"""
    m = _m(d)
    if m.get("financial") or m.get("utility") or not _연간(d):
        return None, None
    끝 = _연간(d)[-1]
    oi, ncl, ta, cl = (_f(끝.get(k)) for k in ("oi", "ncl", "ta", "cl"))
    cap = _raw(d, "cap")
    if oi is None or oi <= 0 or None in (ncl, ta, cl) or cap is None or not _통화맞음(d):
        return None, None
    ev, 자본 = cap + ncl, ta - cl  # type: ignore[operator]
    if ev <= 0 or 자본 <= 0:
        return None, None
    return oi / ev, oi / 자본


def greenblatt(dossiers: list[Dossier], population: list[Dossier]) -> dict[int, Opinion]:
    """그린블랫부(마법공식) — EY·ROC 오름차순 순위의 합을 모집단 백분위로"""
    부분 = {p.stock_id: greenblatt_parts(p) for p in population}
    for d in dossiers:
        부분.setdefault(d.stock_id, greenblatt_parts(d))
    ey = percentile_ranks({k: v[0] for k, v in 부분.items()})
    roc = percentile_ranks({k: v[1] for k, v in 부분.items()})
    합 = percentile_ranks({k: (ey[k] + roc[k]) if k in ey and k in roc else None for k in 부분})
    out: dict[int, Opinion] = {}
    for d in dossiers:
        if d.stock_id not in 합:
            out[d.stock_id] = _없음("greenblatt", "금융·유틸리티 또는 영업이익·자본 근사 없음")
            continue
        e, r = 부분[d.stock_id]
        입장 = 1 if 합[d.stock_id] >= PCT_FOR else -1 if 합[d.stock_id] <= PCT_AGAINST else 0
        out[d.stock_id] = Opinion("greenblatt", 입장, round(abs(합[d.stock_id] - 50) / 50, 6), [_criterion(
            "마법공식 순위", f"{합[d.stock_id]:.0f} 백분위 (EY {_p(e)} · ROC {_p(r)})",
            f"찬성 ≥ {PCT_FOR:g} · 반대 ≤ {PCT_AGAINST:g} "
            "(EY = 영업이익 ÷ (시총 + 비유동부채), ROC = 영업이익 ÷ (총자산 − 유동부채))",
            "financial_snapshots·factors", d.as_of)])  # fmt: skip
    return out


def oneil_earn(dossiers: list[Dossier], population: list[Dossier]) -> dict[int, Opinion]:
    """오닐부(실적 A) 체크리스트 3 — 분기 C 는 판 1 에서 뺀다 (12.3)"""
    out: dict[int, Opinion] = {}
    for d in dossiers:
        연간 = _연간(d)
        끝해 = int(연간[-1]["fy"]) if 연간 else None
        넷 = [_해(d, 끝해 - k) for k in range(3, -1, -1)] if 끝해 is not None else []
        이익 = [_f(r.get("ni")) if r else None for r in 넷]
        증가 = None if len(이익) < 4 or None in 이익 else all(이익[i + 1] > 이익[i] for i in range(3))  # type: ignore[operator]
        g = _cagr3(d)
        roe = _roe(연간[-1]) if 연간 else None
        out[d.stock_id] = _체크_의견("oneil_earn", d, [
            ("3년 연속 이익 증가", 증가, " → ".join(_x(x, 0) for x in 이익) or "—",
             "최근 네 해 순이익이 해마다 늘었다"),
            ("순이익 3년 성장", None if g is None else g >= ONEIL_CAGR, _p(g), f"≥ {ONEIL_CAGR:.0%}"),
            ("ROE", None if roe is None else roe >= ONEIL_ROE, _p(roe), f"≥ {ONEIL_ROE:.0%}"),
        ], "financial_snapshots")  # fmt: skip
    return out


def oneil_price(dossiers: list[Dossier], population: list[Dossier]) -> dict[int, Opinion]:
    """오닐부(주가 N·L) 체크리스트 2 — 둘 다 판정 가능해야 (min(3, 2) = 2)"""
    rs = percentile_ranks({p.stock_id: _raw(p, "mom12_1") for p in population})
    out: dict[int, Opinion] = {}
    for d in dossiers:
        h = _raw(d, "high52")
        r = rs.get(d.stock_id)
        out[d.stock_id] = _체크_의견("oneil_price", d, [
            ("52주 고점 근접", None if h is None else h >= ONEIL_HIGH52, _x(h), f"≥ {ONEIL_HIGH52:g}"),
            ("상대강도(12-1 백분위)", None if r is None else r >= ONEIL_RS, _x(r, 0), f"≥ {ONEIL_RS:g}"),
        ], "factors")  # fmt: skip
    return out


FUNCS = {
    "buffett": buffett, "lynch": lynch, "lynch_check": lynch_check, "graham": graham, "graham_price": graham_price,
    "graham_fin": graham_fin, "greenblatt": greenblatt, "oneil_earn": oneil_earn, "oneil_price": oneil_price,
}  # fmt: skip


def opinions(dossiers: list[Dossier], population: list[Dossier], depts: set[str]) -> dict[int, dict[str, Opinion]]:
    """헌장이 가중을 준 거장 부서만 — {종목: {부서: 의견}}. 모집단은 그날 신호 종목 전체(+ 안건)"""
    모집단 = list({d.stock_id: d for d in [*population, *dossiers]}.values())
    표: dict[int, dict[str, Opinion]] = {d.stock_id: {} for d in dossiers}
    for 부서 in DEPTS:
        if 부서 in depts:
            for sid, o in FUNCS[부서](dossiers, 모집단).items():
                표[sid][부서] = o
    return 표


# ── 거장 계좌 헌장 (docs/company.md 12.4 — 0096 이 그대로 옮긴다, 시험이 대조한다) ────────────────────────

CHARTERS: dict[str, dict[str, Any]] = {
    "ZM": {
        "name": "거장 위원회", "variant": "masters_committee_v1", "lead": "buffett",
        "philosophy": "버핏·린치·그레이엄·그린블랫·오닐의 책 규칙이 함께 회의해 통과한 종목을 산다",
        "weights": {"buffett": 1, "lynch": 1, "graham": 1, "greenblatt": 1, "oneil_earn": 0.5, "oneil_price": 0.5,
                    "governance": 1, "risk": 0.5},
        "veto": ["governance", "risk"], "max_mdd": -0.6, "threshold": 1.0, "agenda": 30, "top_n": 5,
        "sell_rule": "core", "research_mix": None,
    },
    "ZN": {
        "name": "그레이엄 계좌", "variant": "graham_halves_v1", "lead": "graham_price",
        "philosophy": "그레이엄 방어적 투자자 기준(5년 축약판)을 모두 넘은 종목만 산다",
        "weights": {"graham_price": 2, "graham_fin": 2, "governance": 1},
        "veto": ["governance"], "max_mdd": None, "threshold": 2.0, "agenda": 50, "top_n": 5,
        "sell_rule": "core", "research_mix": None,
    },
    "ZL": {
        "name": "린치 계좌", "variant": "lynch_halves_v1", "lead": "lynch",
        "philosophy": "성장 대비 싸고(린치 비율 ≥ 2) 재무가 튼튼한 종목을 산다",
        "weights": {"lynch": 2, "lynch_check": 2, "governance": 1},
        "veto": ["governance"], "max_mdd": None, "threshold": 2.0, "agenda": 50, "top_n": 5,
        "sell_rule": "core", "research_mix": None,
    },
}  # fmt: skip
