"""42회차 u3 ① gap 정의 — 손 계산 작은 세계 (docs/factors.md 12.45 "u3 생존편향 사전 등록", ⓪ 결과 2026-11-09).

묶는 것: 정지 뒤 폐지 · 이전상장 한 계열 · 분할날은 FLUC_RT · 폐지 창 원 종가 비율 · max(0, ·) · 분위 경계 (a) ·
b2 = 스냅숏 · 묶인 탐침 없으면 모름 · 부분 끝 달 제외 · 정적 제외 대칭 · 빌린 상수 = 정의처.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from batch.services import pit_universe as pit
from batch.services import survivorship_gap as sg
from batch.services import universe

ROOT = Path(__file__).resolve().parent.parent
CAP = 200_000_000_000  # 2,000억 — 문턱 1,000억 위
VAL = 1_000_000_000  # 10억 — 문턱 5억 위


def bdays(start: str, end: str) -> list[str]:
    d, e, out = date.fromisoformat(start), date.fromisoformat(end), []
    while d <= e:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


DAYS = bdays("2021-09-01", "2021-11-30")  # 9월 22·10월 21·11월 22 거래일


def _at(v, d):
    return v(d) if callable(v) else v


def stock(code, days, close, *, value=VAL, cap=CAP, name=None, fluc=None, market="KOSPI"):
    """{날: (시장, 원 응답 dict)}. close·value·cap·fluc·market 은 값 또는 날 → 값. fluc 없으면 종가로 낸다."""
    out, prev = {}, None
    for d in days:
        c = _at(close, d)
        f = _at(fluc, d) if fluc is not None else (0.0 if prev is None else (c / prev - 1) * 100)
        v = _at(value, d)
        raw = {"ISU_CD": code, "ISU_NM": _at(name, d) if name is not None else f"종목{code}", "TDD_CLSPRC": c,
               "FLUC_RT": f, "ACC_TRDVAL": v, "MKTCAP": _at(cap, d)}  # fmt: skip
        out[d] = (_at(market, d), raw)
        prev = c
    return out


def world(*stocks):
    resp: dict[str, dict[str, list]] = {}
    for s in stocks:
        for d, (mkt, raw) in s.items():
            resp.setdefault(d.replace("-", ""), {}).setdefault(mkt, []).append(raw)
    return resp


ANCHOR = stock("000010", DAYS, 1000.0)  # 끝까지 평평 — 늘 (a)·(b1)


# ---------------------------------------------------------------------------------------------------------------------
def test_정지_뒤_폐지는_필터로_빼지_않고_마지막_종가로_정산() -> None:
    def h_close(d):
        return 1000.0 if d < "2021-10-01" else (300.0 if d >= "2021-11-11" else 1100.0)

    def h_val(d):
        return VAL if d < "2021-10-15" else 0  # 10-15 부터 정지(행은 있고 거래대금 0)

    H = stock("000020", [d for d in DAYS if d <= "2021-11-11"], h_close, value=h_val,
              cap=lambda d: CAP if d < "2021-10-15" else 50_000_000_000)  # fmt: skip
    # 대조 — 같은 거래대금 꼴인데 10월 무리가 아니었다(9월 말 시총 미달) → 11월 cutoff 에 거래대금 하한으로 빠진다
    C = stock("000030", DAYS, 1000.0, value=h_val, cap=lambda d: 1 if d < "2021-10-01" else CAP)
    r = sg.compute(world(ANCHOR, H, C), snapshot={"000010", "000020", "000030"})

    assert r.members["2021-10"]["a"] == {"000010", "000020"}
    # 11월 cutoff(10-29): H 는 정지·시총 미달·거래대금 평균 4.5억이어도 그대로, C 는 같은 거래대금으로 빠진다
    assert r.members["2021-11"]["a"] == {"000010", "000020"}
    assert r.diagnostics["carried_halted_by_month"] == {"2021-10": 0, "2021-11": 1}
    # 10월: H +10%, 기준 0 → 5% · 11월: H 정지 0 … 11-11 300/1100(마지막 종가로 정산) 뒤 0
    assert r.series["a"]["2021-10"] == pytest.approx(0.05)
    h_nov = 300 / 1100 - 1
    assert r.series["a"]["2021-11"] == pytest.approx(h_nov / 2)
    # b1 = 마지막 응답에 있는 종목만 → 기준 하나
    assert r.members["2021-11"]["b1"] == {"000010"}
    assert r.series["b1"]["2021-11"] == pytest.approx(0.0)
    assert r.diagnostics["disappeared_by_month"] == {"2021-11": 1}
    assert r.diagnostics["big_move_before_disappear_by_month"] == {"2021-11": 1}
    assert r.diagnostics["disappeared_rows"] == len(H)
    assert r.diagnostics["total_rows"] == len(ANCHOR) + len(H) + len(C)
    g = sg.gap(r.series, ["2021-10", "2021-11"])
    assert g.gap_b1 == pytest.approx(sg.cagr_monthly([0.0, 0.0]) - sg.cagr_monthly([0.05, h_nov / 2]))


def test_이전상장은_한_계열이고_사라진_것으로_세지_않는다() -> None:
    T = stock("000040", DAYS, lambda d: 1000.0 if d < "2021-10-18" else 1100.0,
              market=lambda d: "KOSDAQ" if d < "2021-10-18" else "KOSPI")  # fmt: skip
    r = sg.compute(world(ANCHOR, T), snapshot=set())
    assert "000040" in r.members["2021-11"]["a"] and "000040" in r.members["2021-11"]["b1"]
    assert r.diagnostics["disappeared_by_month"] == {}
    assert r.series["a"]["2021-10"] == pytest.approx(0.05)


def test_분할날은_FLUC_RT_로_가짜_수익이_없다() -> None:
    S = stock("000050", DAYS, lambda d: 1000.0 if d < "2021-10-12" else 500.0, fluc=0.0)  # 2:1 분할, 기준가도 반
    r = sg.compute(world(ANCHOR, S), snapshot=set())
    assert r.series["a"]["2021-10"] == pytest.approx(0.0)
    assert r.diagnostics["diverged_pairs"] == 0


def test_폐지_창_안에서만_원_종가_비율() -> None:
    def d_close(d):
        if d < "2021-09-15":
            return 2000.0
        if d < "2021-10-29":
            return 1000.0  # 9-15 분할(창 밖 — 갈려도 FLUC_RT 그대로)
        if d < "2021-11-04":
            return 1010.0  # 10-29 원 비율 +1.0% 대 FLUC 0.1% — 0.9%p, 1%p 안 넘음
        return 100.0  # 11-04 정리매매 기준가 재설정 — FLUC 0 인데 원 비율 −90%

    def d_fluc(d):
        return 0.1 if d == "2021-10-29" else 0.0

    D = stock("000060", [d for d in DAYS if d <= "2021-11-05"], d_close, fluc=d_fluc)
    r = sg.compute(world(ANCHOR, D), snapshot=set())
    assert r.diagnostics["diverged_pairs"] == 1
    assert r.series["a"]["2021-11"] == pytest.approx((0.0 + (100 / 1010 - 1)) / 2)
    assert "2021-09" not in r.series["a"]  # 9월은 cutoff 가 없어 계열 없음
    assert r.series["a"]["2021-10"] == pytest.approx(0.001 / 2)  # 창 안이지만 0.9%p 라 FLUC_RT


def test_max0_와_가장_나쁜_분위() -> None:
    assert sg.adjusted_cagr(0.20, -0.05) == pytest.approx(0.20)
    assert sg.adjusted_cagr(0.20, 0.05) == pytest.approx(0.15)
    assert sg.adjusted_cagr(0.20, None) is None
    q_small = {1: 0.01, 2: 0.0, 3: -0.01, 4: 0.015, 5: 0.0}
    assert sg.trimmed_excess(0.10, 0.02, q_small) == pytest.approx(0.10)  # max_q − gap < 0 → 깎지 않음
    q_big = {1: 0.05, 2: 0.0, 3: -0.01, 4: 0.03, 5: 0.0}
    assert sg.trimmed_excess(0.10, 0.02, q_big) == pytest.approx(0.07)
    assert sg.worst_quintile(q_big) == 1
    assert sg.trimmed_excess(0.10, 0.02, {**q_big, 3: None}) is None


def test_분위_경계는_a_로_b2_는_스냅숏() -> None:
    stocks = [stock(f"0001{i}0", DAYS, 1000.0, cap=i * 100_000_000_000) for i in range(1, 11)]
    small = stock("000200", DAYS, 1000.0, cap=1)  # 스냅숏에 있지만 (a) 밖
    snap = {f"0001{i}0" for i in range(6, 11)} | {"000200"}
    r = sg.compute(world(*stocks, small), snapshot=snap)
    m = r.members["2021-10"]
    code = {i: f"0001{i}0" for i in range(1, 11)}
    assert m["a_q1"] == {code[1], code[2]} and m["a_q5"] == {code[9], code[10]}
    assert m["b2"] == {code[i] for i in range(6, 11)} and "000200" not in m["b2"]
    assert m["b2_q1"] == set() and m["b2_q2"] == set()
    assert m["b2_q3"] == {code[6]} and m["b2_q4"] == {code[7], code[8]} and m["b2_q5"] == {code[9], code[10]}
    assert r.series["b2_q1"]["2021-10"] is None  # 빈 무리 → 그 분위 gap 모름
    assert sg.gap(r.series, ["2021-10"]).gap_q[1] is None


def test_묶인_탐침이_없으면_모름() -> None:
    assert sg.excess_verdict("통과", 0.05, tied_probe=False) == sg.VERDICT_UNKNOWN == "모름(생존편향)"
    assert sg.excess_verdict("통과", 0.0, tied_probe=True) == sg.VERDICT_UNKNOWN
    assert sg.excess_verdict("통과", None, tied_probe=True) == sg.VERDICT_UNKNOWN
    assert sg.excess_verdict("통과", 0.01, tied_probe=True) == "통과"


def test_부분_끝_달은_뺀다() -> None:
    series = {"a": {"2021-10": 0.01, "2021-11": 0.5}, "b2": {"2021-10": 0.02, "2021-11": -0.5},
              "b1": {"2021-10": 0.02, "2021-11": 0.0}}  # fmt: skip
    for q in range(1, 6):
        series[f"a_q{q}"] = series["a"]
        series[f"b2_q{q}"] = series["b2"]
    last = {"2021-10": "2021-10-29", "2021-11": "2021-11-30"}
    g = sg.gap(series, ["2021-10", "2021-11"], month_last_day=last, row_last_date="2021-11-15")
    assert g.months == ("2021-10",)
    assert g.gap == pytest.approx(1.02**12 - 1.01**12)
    full = sg.gap(series, ["2021-10", "2021-11"], month_last_day=last, row_last_date="2021-11-30")
    assert full.months == ("2021-10", "2021-11")
    assert full.gap == pytest.approx((1.02 * 0.5) ** 6 - (1.01 * 1.5) ** 6)
    assert sg.gap(series, ["2021-12"]).gap is None  # 탐침 밖 달 → 모름


def test_정적_제외는_세_무리에_대칭() -> None:
    spac = stock("000300", DAYS, 1000.0, name=lambda d: "가나스팩1호" if d < "2021-10-01" else "가나바이오")
    pref = stock("000305", DAYS, 1000.0)  # 끝자리 5 → 우선주
    r = sg.compute(world(ANCHOR, spac, pref), snapshot={"000010", "000300", "000305"})
    for m in ("2021-10", "2021-11"):
        for g in ("a", "b1", "b2"):
            assert r.members[m][g] == {"000010"}
    assert r.diagnostics["static_excluded"] == 2
    assert sg.is_preferred("005935") and not sg.is_preferred("005930") and sg.is_preferred("00104K")


def test_상장_1년_가장자리와_거래대금_모름은_통과() -> None:
    near = stock("000400", [d for d in DAYS if d >= "2021-09-06"], 1000.0)  # 5일 — 가장자리
    far = stock("000410", [d for d in DAYS if d >= "2021-09-09"], 1000.0)  # 8일 — 첫 관측부터 365일
    unknown = stock("000420", DAYS, 1000.0, value=lambda d: 1 if d in DAYS[-25:-20] else None)  # 아는 날 5 < 10
    thin = stock("000430", DAYS, 1000.0, value=1)
    r = sg.compute(world(ANCHOR, near, far, unknown, thin), snapshot=set())
    assert r.members["2021-11"]["a"] == {"000010", "000400", "000420"}


def test_cutoff_은_전달_마지막_거래일이어야() -> None:
    w = world(ANCHOR)
    assert sg.month_cutoffs(d.replace("-", "") for d in DAYS) == {"2021-10": "2021-09-30", "2021-11": "2021-10-29"}
    with pytest.raises(ValueError):
        sg.compute(w, snapshot=set(), cutoffs={"2021-10": "2021-09-29"})
    r = sg.compute(w, snapshot=set(), cutoffs={"2021-11": "20211029"})
    assert list(r.members) == ["2021-11"]


def test_KrxDailyRow_도_받는다() -> None:
    from batch.sources.krx import KrxDailyRow

    raw = {"BAS_DD": "20210930", "ISU_CD": "000010", "ISU_NM": "가", "TDD_CLSPRC": "1,000", "FLUC_RT": "-1.5",
           "ACC_TRDVAL": "0", "MKTCAP": "200000000000"}  # fmt: skip
    a, b = sg.to_row(KrxDailyRow.from_raw(raw)), sg.to_row(raw)
    assert a == b and a.close == 1000.0 and a.change_pct == -1.5 and a.value == 0.0


def test_빌린_상수는_정의처와_같다() -> None:
    f = universe.UniverseFilters.korea()
    assert f.min_market_cap == sg.MIN_MARKET_CAP and f.min_avg_turnover_20d == sg.MIN_AVG_TURNOVER_20D
    assert sg.MIN_LISTED_DAYS == f.min_listed_days == universe.DEFAULT_MIN_LISTED_DAYS
    assert sg.HISTORY_EDGE_DAYS == pit.HISTORY_EDGE_DAYS == 7  # 12.45 "7일 안"
    assert sg.TURNOVER_DAYS == pit.TURNOVER_DAYS == 20
    assert sg.is_spac is universe.is_spac
    assert sg.PROBE_START == "2021-09-01" and sg.DELIST_WINDOW == 20
    spec = importlib.util.spec_from_file_location("probe_survivorship_0", ROOT / "scripts" / "probe_survivorship_0.py")
    assert spec and spec.loader
    probe = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("probe_survivorship_0", probe)
    spec.loader.exec_module(probe)
    for code in ("005930", "005935", "00104K", "0001A0", "12345", "1234567", ""):
        assert sg.is_preferred(code) == probe.preferred_by_code(code), code


def test_전략_점수_경로가_이_모듈을_import_하지_않는다() -> None:
    걸림 = [str(p.relative_to(ROOT)) for p in (ROOT / "batch").rglob("*.py")
            if p.name != "survivorship_gap.py" and "survivorship_gap" in p.read_text(encoding="utf-8")]  # fmt: skip
    assert 걸림 == []


def test_cagr_monthly() -> None:
    assert sg.cagr_monthly([0.01] * 12) == pytest.approx(1.01**12 - 1)
    assert sg.cagr_monthly([]) is None and sg.cagr_monthly([-1.0]) is None
    assert math.isclose(sg.cagr_monthly([0.1, -0.1]), (1.1 * 0.9) ** 6 - 1)
