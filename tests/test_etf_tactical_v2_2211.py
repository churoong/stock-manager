"""45회차 (나) `etf_tactical_v2`·`etf_theme_v1` 순수 함수 (docs/factors.md 12.48 사전 등록) — 손으로 셀 수 있는 계열."""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from batch.services import etf_tactical as et
from batch.services import etf_tactical_v2 as v2
from batch.services import etf_theme as th

뿌리 = Path(__file__).resolve().parent.parent


def _월말(값: list[float], 첫해: int = 2023, 첫달: int = 1) -> list[tuple[str, float]]:
    """한 달에 한 행(그달 28일) — 월말 종가만 있는 계열"""
    out = []
    for k, v in enumerate(값):
        m = 첫달 - 1 + k
        out.append((f"{첫해 + m // 12:04d}-{m % 12 + 1:02d}-28", v))
    return out


def _다음달_1일(s: list[tuple[str, float]]) -> str:
    y, m = int(s[-1][0][:4]), int(s[-1][0][5:7])
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}-01"


def _평일(n: int, 값=lambda i: 100.0, 첫날: date = date(2024, 1, 1)) -> list[tuple[str, float]]:
    out, d = [], 첫날
    while len(out) < n:
        if d.weekday() < 5:
            out.append((d.isoformat(), float(값(len(out)))))
        d += timedelta(days=1)
    return out


def _마구(n: int = 900, seed: int = 7) -> list[tuple[str, float]]:
    rnd, c = random.Random(seed), 100.0
    out = []
    for d, _v in _평일(n):
        c *= 1 + rnd.gauss(0.0002, 0.015)
        out.append((d, c))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# 국면 — 손 계산
# ---------------------------------------------------------------------------------------------------------------------
def test_10개월_평균_이상이면_상승_98() -> None:
    s = _월말([float(i) for i in range(1, 11)])  # 10개 월말, 평균 5.5, 끝 10
    r = v2.regime_v2(s, _다음달_1일(s))
    assert r.state == v2.UP and r.ma10 == pytest.approx(5.5) and r.close == 10.0 and r.ret12 is None
    assert et.target(r.state) == {"long": 0.98}


def test_평균과_같으면_상승이다() -> None:
    s = _월말([10.0] * 10)
    assert v2.regime_v2(s, _다음달_1일(s)).state == v2.UP


def test_하락이면서_12개월_수익이_음일_때만_인버스() -> None:
    아래 = [10.0] * 11 + [9.0]  # 끝 9 < 최근 10개 평균 9.9
    # 12개월 전 12 → 9/12 − 1 < 0 → 인버스 50%
    s = _월말([12.0] + 아래)
    r = v2.regime_v2(s, _다음달_1일(s))
    assert r.ma10 == pytest.approx(9.9) and r.ret12 == pytest.approx(9 / 12 - 1)
    assert r.state == v2.DOWN and et.target(r.state) == {"inverse": 0.5}
    # 12개월 전 8 → 수익 > 0 → 현금
    s = _월말([8.0] + 아래)
    r = v2.regime_v2(s, _다음달_1일(s))
    assert r.state == v2.MIXED and et.target(r.state) == {}
    # 12개월 전 9 → 수익 0 — '< 0' 이 아니므로 현금
    s = _월말([9.0] + 아래)
    assert v2.regime_v2(s, _다음달_1일(s)).state == v2.MIXED


def test_하락인데_13개_월말이_없으면_모름_현금() -> None:
    s = _월말([10.0] * 11 + [9.0])  # 월말 12개
    r = v2.regime_v2(s, _다음달_1일(s))
    assert r.state == v2.UNKNOWN and et.target(r.state) == {} and "13" in r.reason


def test_끝난_월말이_10개_미만이면_모름() -> None:
    s = _월말([float(i) for i in range(1, 10)])
    assert v2.regime_v2(s, _다음달_1일(s)).state == v2.UNKNOWN


def test_as_of_달의_행과_as_of_당일은_보지_않는다() -> None:
    s = _월말([float(i) for i in range(1, 11)])
    as_of = "2023-11-10"
    더 = s + [("2023-11-03", 1.0), ("2023-11-10", 1.0), ("2023-11-20", 1.0)]  # 덜 찬 달 — 끝난 월말이 아니다
    r = v2.regime_v2(더, as_of)
    assert r.month_end == "2023-10-28" and r.close == 10.0 and r.last_row == "2023-11-03" and r.state == v2.UP


def test_빈_달이_끼면_모름() -> None:
    s = _월말([float(i) for i in range(1, 13)])
    빠짐 = [x for x in s if not x[0].startswith("2023-06")]  # 6월 행이 아예 없다
    as_of = "2024-01-01"
    assert v2.regime_v2(s, as_of).state == v2.UP
    assert v2.regime_v2(빠짐, as_of).state == v2.UNKNOWN


def test_묵음은_현금이고_7일은_아직_묵음이_아니다() -> None:
    s = _월말([float(i) for i in range(1, 11)])  # 마지막 행 2023-10-28
    assert v2.regime_v2(s, "2023-11-04").state == v2.UP  # 7일
    assert v2.regime_v2(s, "2023-11-05").state == v2.UNKNOWN  # 8일


def test_달_중간_묵음은_그날_현금이고_풀리면_그달_판정() -> None:
    s = _월말([float(i) for i in range(1, 11)]) + [("2023-11-01", 5.0)]
    assert v2.regime_v2(s, "2023-11-02").state == v2.UP
    assert v2.regime_v2(s, "2023-11-12").state == v2.UNKNOWN  # 11-01 뒤로 11일 묵음
    풀림 = s + [("2023-11-13", 1.0)]
    r = v2.regime_v2(풀림, "2023-11-14")
    assert r.state == v2.UP and r.close == 10.0  # 달 안 값(1.0)이 아니라 끝난 월말로


def test_운영_240행이면_인버스가_막히고_300행이면_열린다() -> None:
    내림 = lambda i: 1000.0 - i  # noqa: E731
    for n, 기대 in ((240, v2.UNKNOWN), (300, v2.DOWN)):
        s = _평일(n + 1, 내림)
        보는, as_of = s[:n], s[n][0]
        r = v2.regime_v2(보는, as_of)
        assert r.state == 기대, (n, len(v2.month_ends(보는, as_of)))


def test_공통_시작은_13번째_월말_다음_거래일() -> None:
    s = _평일(400)
    시작 = v2.common_start(s)
    assert 시작 == "2025-02-03"  # 2024-01 ~ 2025-01 월말 13개, 그 다음 거래일
    r = v2.regime_v2(s[: [d for d, _ in s].index(시작)], 시작)
    assert len(v2.month_ends(s, 시작)) == 13 and r.ret12 is not None


# ---------------------------------------------------------------------------------------------------------------------
# 리플레이
# ---------------------------------------------------------------------------------------------------------------------
def test_판1_어댑터는_판1_replay_와_비트까지_같다() -> None:
    for seed in (1, 2, 3):
        s = _마구(700, seed)
        a = et.replay(s)
        b = v2.replay(s, v2.v1_regime, start=s[et.SMA_DAYS + 1][0])
        for k, v in a.items():
            assert b[k] == v, k


def test_빠른_자르기는_전체_행과_같은_값() -> None:
    s = _마구(900, 5)
    a = v2.replay(s)
    b = v2.replay(s, lambda rows, as_of: v2.regime_v2(rows, as_of))
    assert {k: a[k] for k in a} == {k: b[k] for k in b}
    assert a["regime_changes"] > 0


def test_갈아탐은_달_첫_거래일과_그_다음_거래일에만() -> None:
    s = _마구(900, 11)
    날짜 = [d for d, _ in s]
    r = v2.replay(s)
    상태 = [v2.regime_v2(s[: i + 1], s[i + 1][0]).state for i in range(날짜.index(r["from"]), len(s) - 1)]
    as_ofs = 날짜[날짜.index(r["from"]) + 1 :]
    for k in range(1, len(상태)):
        if 상태[k] != 상태[k - 1]:
            assert as_ofs[k][:7] != as_ofs[k - 1][:7], as_ofs[k]  # 이어진 계열이면 국면은 달이 바뀔 때만 바뀐다
    assert r["regime_changes"] > 0 and r["switches"] >= r["regime_changes"] and r["stale_gaps"] == 0


def _가짜_국면(표: dict[str, str]):
    return lambda rows, as_of: v2.RegimeV2(표.get(as_of, v2.UNKNOWN), as_of, None, None, None, None, None)


def test_뒤집힘_수와_상승날_현금_인버스_몫_손으로() -> None:
    s = [("2024-01-01", 100.0), ("2024-01-02", 110.0), ("2024-01-03", 121.0), ("2024-01-04", 108.9),
         ("2024-01-05", 119.79), ("2024-01-08", 131.769)]  # fmt: skip
    # as_of 마다: 상승 → 하락 → 하락 → 하락 → 모름
    표 = {"2024-01-02": v2.UP, "2024-01-03": v2.DOWN, "2024-01-04": v2.DOWN, "2024-01-05": v2.DOWN, "2024-01-08": "x"}
    r = v2.replay(s, _가짜_국면(표), cost=0.0, start="2024-01-02")
    # 날: 01-02 long 사서 +10% · 01-03 long 팜(현금) +10% · 01-04 인버스 삼 −10% · 01-05 인버스 +10% · 01-08 인버스 팜 +10%
    assert r["switches"] == 4 and r["regime_changes"] == 2 and r["days"] == 5
    기대 = (1 + 0.98 * 0.1) * 1.0 * (1 + 0.5 * 0.1) * (1 - 0.5 * (119.79 / 108.9 - 1)) * 1.0
    assert r["twr_pct"] == pytest.approx((기대 - 1) * 100)
    assert r["up_days"] == 4  # 01-02·01-03·01-05·01-08 이 올랐다
    assert r["up_cash_share"] == pytest.approx(2 / 4) and r["up_inverse_share"] == pytest.approx(1 / 4)
    assert r["up_not_long_share"] == pytest.approx(3 / 4)
    연수 = (date(2024, 1, 8) - date(2024, 1, 1)).days / 365.25
    assert r["flips_per_year"] == pytest.approx(4 / 연수)
    assert [d for d, _ in r["equity"]] == [d for d, _ in s] and r["equity"][0][1] == 1.0


def test_비용은_바뀐_비중_1당_0_12퍼센트() -> None:
    s = [("2024-01-01", 100.0), ("2024-01-02", 100.0)]
    r = v2.replay(s, _가짜_국면({"2024-01-02": v2.UP}), start="2024-01-02")
    assert r["twr_pct"] == pytest.approx(-0.0012 * 0.98 * 100)


def test_인버스_시세가_있으면_그것을_없으면_음의_지수() -> None:
    s = [("2024-01-01", 100.0), ("2024-01-02", 100.0), ("2024-01-03", 90.0)]
    표 = {"2024-01-02": v2.DOWN, "2024-01-03": v2.DOWN}
    대리 = v2.replay(s, _가짜_국면(표), cost=0.0, start="2024-01-02")
    assert 대리["twr_pct"] == pytest.approx(0.5 * 0.1 * 100)
    sh = [("2024-01-02", 50.0), ("2024-01-03", 54.0)]
    실제 = v2.replay(s, _가짜_국면(표), cost=0.0, start="2024-01-02", inverse_series=sh)
    assert 실제["twr_pct"] == pytest.approx(0.5 * 0.08 * 100)


def test_묵음_구간을_센다() -> None:
    s = [("2024-01-01", 100.0), ("2024-01-02", 100.0), ("2024-01-15", 100.0)]
    r = v2.replay(s, _가짜_국면({}), start="2024-01-02")
    assert r["stale_gaps"] == 1


# ---------------------------------------------------------------------------------------------------------------------
# 홀드아웃 문·계좌 판정
# ---------------------------------------------------------------------------------------------------------------------
def _결과(**kw) -> dict:
    base = {"days": 100, "from": "2001-01-31", "to": "2019-04-30", "twr_pct": 10.0, "hold_pct": 50.0,
            "flips_per_year": 3.0, "mdd_pct": -7.0, "hold_mdd_pct": -10.0, "regime_changes": 5,
            "up_not_long_share": 0.2, "stale_gaps": 0}  # fmt: skip
    return {**base, **kw}


def test_홀드아웃_문_경계() -> None:
    판1 = _결과(twr_pct=5.0, flips_per_year=30.0)
    g = v2.holdout_gate(_결과(), 판1, "X", "2019-05-02")
    assert g["pass"] and g["a"] and g["b"] and g["c"] and not g["literature_sample"]
    # (a) 같으면 못 넘음
    assert not v2.holdout_gate(_결과(twr_pct=5.0), 판1, "X")["a"]
    # (b) 같으면 못 넘음
    assert not v2.holdout_gate(_결과(flips_per_year=30.0), 판1, "X")["b"]
    # (c) 음수 규약 — −7 ≥ 0.7 × −10 넘음, −7.01 못 넘음, −3 넘음(낙폭이 작다), 부호를 거꾸로 견주면 −12 가 넘어 버린다
    assert v2.holdout_gate(_결과(mdd_pct=-7.0), 판1, "X")["c"]
    assert not v2.holdout_gate(_결과(mdd_pct=-7.01), 판1, "X")["c"]
    assert v2.holdout_gate(_결과(mdd_pct=-3.0), 판1, "X")["c"]
    assert not v2.holdout_gate(_결과(mdd_pct=-12.0), 판1, "X")["c"]
    assert v2.holdout_gate(_결과(), 판1, "Y")["literature_sample"]


def test_홀드아웃_문은_공통_구간과_홀드아웃_안만() -> None:
    판1 = _결과(twr_pct=5.0, flips_per_year=30.0)
    with pytest.raises(ValueError, match="공통 구간"):
        v2.holdout_gate(_결과(to="2019-04-29"), 판1, "X")
    with pytest.raises(ValueError, match="홀드아웃"):
        v2.holdout_gate(_결과(**{"from": "1999-12-31"}), _결과(**{"from": "1999-12-31"}), "X")
    with pytest.raises(ValueError, match="홀드아웃"):
        v2.holdout_gate(_결과(), 판1, "X", operating_first="2019-04-30")


def test_홀드아웃_자료는_2000년부터_운영_첫_행_전날까지() -> None:
    s = [("1999-12-31", 1.0), ("2000-01-03", 2.0), ("2019-05-01", 3.0), ("2019-05-02", 4.0)]
    assert v2.holdout_rows(s, "2019-05-02") == [("2000-01-03", 2.0), ("2019-05-01", 3.0)]


def test_계좌_판정() -> None:
    j = v2.account_judgement
    assert j(125, 0.0, 0.0, -1.0, -10.0, 3) == "대기"
    assert j(126, 45.0, 50.0, -7.0, -10.0, 3) == "통과"  # 보유 − 5%p 경계 · 0.7× 경계
    assert j(126, 44.99, 50.0, -7.0, -10.0, 3) == "실패"
    assert j(126, 60.0, 50.0, -7.01, -10.0, 3) == "실패"
    assert j(126, 60.0, 50.0, -1.0, -10.0, 0).startswith("판정 불가(퇴화)")
    assert j(200, 60.0, 50.0, -1.0, -10.0, 0, degenerate_at_126=True) == "대기"
    assert j(252, 60.0, 50.0, -1.0, -10.0, 0, degenerate_at_126=True) == "판정 불가"
    assert j(252, 60.0, 50.0, -1.0, -10.0, 1, degenerate_at_126=True) == "통과"


# ---------------------------------------------------------------------------------------------------------------------
# 테마 — 순위·거르개·건너뜀·몫
# ---------------------------------------------------------------------------------------------------------------------
def _업종(끝: float, 첫: float = 100.0, n: int = 13) -> list[tuple[str, float]]:
    return _월말([첫] * (n - 1) + [끝])


AS_OF = "2024-02-01"  # _월말 13개면 2023-01 ~ 2024-01


def test_상위_3_과_동률은_티커순() -> None:
    sec = {"XLK": _업종(130), "XLE": _업종(120), "XLB": _업종(120), "XLF": _업종(110), "XLU": _업종(90)}
    r = th.rank(sec, AS_OF)
    assert [t for t, _ in r] == ["XLK", "XLB", "XLE", "XLF", "XLU"]  # XLB·XLE 동률 → 글자순
    assert r[0][1] == pytest.approx(0.3)
    t = th.targets(v2.UP, r)
    assert t == {0: ("XLK", 0.98 / 3), 1: ("XLB", 0.98 / 3), 2: ("XLE", 0.98 / 3)}


def test_음수_몫은_SPY_0은_그대로_모자라면_SPY() -> None:
    r = th.rank({"XLK": _업종(100), "XLE": _업종(90)}, AS_OF)  # XLK 0, XLE −10%
    t = th.targets(v2.UP, r)
    assert [t[k][0] for k in range(3)] == ["XLK", "SPY", "SPY"]


def test_13개_월말이_안_찬_업종은_빠지고_차면_들어온다() -> None:
    sec = {"XLK": _업종(110)}
    sec["XLRE"] = _월말([100.0] * 11 + [200.0], 첫해=2023, 첫달=2)  # 2023-02 ~ 2024-01, 12개
    assert [t for t, _ in th.rank(sec, AS_OF)] == ["XLK"]
    sec["XLRE"] = _월말([100.0] * 12 + [200.0], 첫해=2023, 첫달=1)
    assert [t for t, _ in th.rank(sec, AS_OF)] == ["XLRE", "XLK"]


def test_하락_모름은_짝과_같다() -> None:
    assert th.targets(v2.DOWN, []) == {"inv": ("SH", 0.5)}
    assert th.targets(v2.MIXED, []) == {} and th.targets(v2.UNKNOWN, []) == {}


def test_레버리지_인버스_합성_옵션은_뺀다() -> None:
    assert th.us_excluded("Financial Select Sector SPDR Fund", "Financial") is None
    assert th.us_excluded("Direxion Daily Financial Bull 3X Shares") == "레버리지·인버스"
    assert th.us_excluded("Something", "Trading--Leveraged Equity") == "레버리지·인버스"
    assert th.us_excluded("ProShares Short S&P500") == "레버리지·인버스"
    assert th.us_excluded("Invesco S&P 500 Synthetic") == "합성"
    assert th.us_excluded("Global X S&P 500 Covered Call ETF") == "옵션 전략"
    meta = {"XLK": {"name": "Technology Select Sector SPDR Fund 2X"}}
    assert [t for t, _ in th.rank({"XLK": _업종(130), "XLE": _업종(110)}, AS_OF, meta=meta)] == ["XLE"]
    # 국내판(기록만) 이름 거르개 — etf.py 표기 + '선물'
    assert th.kr_excluded("KODEX 반도체") is None
    assert th.kr_excluded("KODEX 200선물인버스2X") is not None
    assert th.kr_excluded("TIGER 2차전지 레버리지") is not None
    assert th.kr_excluded("KODEX 합성-미국바이오") is not None
    assert th.kr_excluded("KODEX 미국S&P500선물").startswith("선물")


def test_재수집_대기_달은_다시_맞추지_않는다() -> None:
    sec = {"XLK": _업종(130), "XLE": _업종(120), "XLB": _업종(110), "XLF": _업종(105)}
    up = v2.RegimeV2(v2.UP, AS_OF, None, None, None, None, None)
    든 = {0: ("XLF", 0.3), 1: ("XLB", 0.3), 2: ("SPY", 0.3)}
    plan, skip, _r = th.month_plan(up, sec, 든, pending={"XLE"})
    assert skip and plan == 든
    # 순위에 들지 않는 ETF(이력 없음) 대기는 건너뛰지 않는다
    plan, skip, _r = th.month_plan(up, sec, 든, pending={"XLC"})
    assert not skip and plan[0][0] == "XLK"
    # 하락 달에는 순위를 안 쓰므로 빠져나간다
    down = v2.RegimeV2(v2.DOWN, AS_OF, None, None, None, None, None)
    plan, skip, _r = th.month_plan(down, sec, 든, pending={"XLE"})
    assert not skip and plan == {"inv": ("SH", 0.5)}


def test_몫은_다른_것만_팔고_판_날은_사지_않는다() -> None:
    w = 0.98 / 3
    든 = {0: ("XLK", w), 1: ("XLE", w), 2: ("XLB", w)}
    want = {0: ("XLK", w), 1: ("XLF", w), 2: ("XLB", w)}
    새, 변화, n = th.trade(든, want)
    assert 새 == {0: ("XLK", w), 2: ("XLB", w)} and n == 1 and 변화 == pytest.approx(w)
    새, 변화, n = th.trade(새, want)
    assert 새 == want and n == 1
    # 인버스 → 상승: 오늘은 SH 만 팔고 다음 날 세 몫
    새, _v, n = th.trade({"inv": ("SH", 0.5)}, want)
    assert 새 == {} and n == 1
    assert th.trade(새, want)[0] == want


def _테마_계열():
    idx = _마구(800, 3)
    rnd = random.Random(9)
    sec = {}
    for t in th.SECTORS:
        c, out = 50.0, []
        for d, _ in idx:
            c *= 1 + rnd.gauss(0.0003, 0.013)
            out.append((d, c))
        sec[t] = out
    spy = [(d, c * 0.5) for d, c in idx]
    return idx, sec, spy


def test_테마_리플레이_건너뜀과_짝_구간() -> None:
    idx, sec, spy = _테마_계열()
    a = th.replay(idx, sec, spy)
    pair = v2.replay(idx, long_series=spy)
    assert a["from"] == pair["from"] and a["to"] == pair["to"] and a["days"] == pair["days"]
    assert a["up_months"] > 0 and a["skipped_months"] == []
    달 = sorted({d[:7] for d, _ in idx if d >= a["from"]})
    b = th.replay(idx, sec, spy, pending_by_month={m: set(th.SECTORS) for m in 달})
    assert b["skipped_months"] and set(b["skipped_months"]) <= set(달)
    assert b["trades"] < a["trades"]


def test_업종이_전부_SPY_와_같으면_테마는_짝과_같다() -> None:
    idx, _sec, spy = _테마_계열()
    sec = {t: spy for t in th.SECTORS}
    a, pair = th.replay(idx, sec, spy), v2.replay(idx, long_series=spy)
    assert a["twr_pct"] == pytest.approx(pair["twr_pct"], rel=1e-9) and a["mdd_pct"] == pytest.approx(pair["mdd_pct"])


def test_이등분() -> None:
    eq = [("d0", 1.0), ("d1", 1.1), ("d2", 1.21), ("d3", 1.21 * 0.5)]
    a, b = th.halves(eq)  # 3날 → 앞 1날, 뒤 2날
    assert a == pytest.approx(0.1) and b == pytest.approx(1.21 * 0.5 / 1.1 - 1)


def _곡선(값: list[float]) -> list[tuple[str, float]]:
    return [(f"d{i}", v) for i, v in enumerate(값)]


def test_테마_홀드아웃_문() -> None:
    짝 = {"days": 4, "from": "a", "to": "b", "twr_pct": 10.0, "hold_mdd_pct": -50.0,
          "equity": _곡선([1.0, 1.05, 1.05, 1.1])}  # fmt: skip
    좋음 = {**짝, "twr_pct": 20.0, "mdd_pct": -35.0, "equity": _곡선([1.0, 1.1, 1.1, 1.2])}
    g = th.holdout_gate(좋음, 짝, pair_gate_passed=True)
    assert g["pass"] and g["i"] and g["ii"] and g["iii"]
    assert not th.holdout_gate(좋음, 짝, pair_gate_passed=False)["pass"]  # 짝이 못 넘으면 열지 않는다
    뒤만 = {**좋음, "equity": _곡선([1.0, 1.0, 1.0, 1.2])}  # 앞 반 0 < 짝 앞 반 5%
    assert not th.holdout_gate(뒤만, 짝, True)["ii"]
    assert not th.holdout_gate({**좋음, "mdd_pct": -35.01}, 짝, True)["iii"]
    with pytest.raises(ValueError):
        th.holdout_gate({**좋음, "to": "c"}, 짝, True)


def test_그림자_탈락_점검() -> None:
    spy = [1.0, 0.5, 1.0]  # MDD −50
    짝 = [1.0, 1.0, 1.25]
    r = th.shadow_check([1.0, 1.0, 1.2], 짝, spy, 3)
    assert r["excess_pp"] == pytest.approx(-5.0) and r["verdict"] == "접음" and r["n_up_months"] == 3
    r = th.shadow_check([1.0, 0.9, 1.3], 짝, spy, 3)
    assert r["excess_pp"] == pytest.approx(5.0) and r["mdd_pct"] == pytest.approx(-10.0)
    assert r["spy_mdd_pct"] == pytest.approx(-50.0) and r["verdict"] == "유지(통과 판정 아님)"


def test_그림자_탈락_점검_경계(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = [1.0, 0.5, 1.0]  # MDD −50 → 0.7× = −35
    짝 = [1.0, 1.0, 1.25]
    # −3%p: −2.9 유지, −3.1 접음
    assert th.shadow_check([1.0, 1.0, 1.221], 짝, spy, 3)["verdict"] == "유지(통과 판정 아님)"
    assert th.shadow_check([1.0, 1.0, 1.219], 짝, spy, 3)["verdict"] == "접음"
    # 정확한 경계(2진수로 떨어지는 값): 초과가 HARM_PP 와 같으면 접지 않는다
    monkeypatch.setattr(th, "HARM_PP", -25.0)
    assert th.shadow_check([1.0, 1.0, 1.0], 짝, spy, 3)["verdict"] == "유지(통과 판정 아님)"
    assert th.shadow_check([1.0, 1.0, 0.99], 짝, spy, 3)["verdict"] == "접음"
    monkeypatch.setattr(th, "HARM_PP", -3.0)
    # MDD: |−25| ≤ 0.5 × |−50| 유지(경계), 낙폭이 더 크면 접음 (비율을 2진수로 떨어지는 0.5 로)
    monkeypatch.setattr(v2, "MDD_RATIO", 0.5)
    assert th.shadow_check([1.0, 0.75, 1.25], 짝, spy, 3)["verdict"] == "유지(통과 판정 아님)"
    assert th.shadow_check([1.0, 0.74, 1.25], 짝, spy, 3)["verdict"] == "접음"
    # 상승 달 0 이면 판정 불가(탈락 아님)
    assert th.shadow_check([1.0, 0.1, 0.1], 짝, spy, 0)["verdict"] == "판정 불가(탈락 아님)"


# ---------------------------------------------------------------------------------------------------------------------
# 상수 — 12.48 사전 등록 글과 같다
# ---------------------------------------------------------------------------------------------------------------------
def test_상수가_12_48_과_같다() -> None:
    글 = (뿌리 / "docs" / "factors.md").read_text(encoding="utf-8")
    절 = 글[글.index("### 12.48") :]
    assert v2.MA_MONTHS == 10 and "최근 10개 월말 종가의 산술평균" in 절
    assert v2.RET_MONTHS == 12 and "12개 월말 전 종가" in 절 and "13개 월말" in 절
    assert et.LONG_WEIGHT == 0.98 and "`LONG_WEIGHT` 0.98" in 절
    assert et.INVERSE_WEIGHT == 0.5 and "`INVERSE_WEIGHT` 0.5" in 절
    assert et.STALE_DAYS == 7 and "`STALE_DAYS` 7" in 절
    assert v2.COST == 0.0012 and "replay(cost=0.0012)" in 절
    import inspect

    assert inspect.signature(et.replay).parameters["cost"].default == v2.COST  # 판 1 비용 그대로
    assert v2.MDD_RATIO == 0.7 and "0.7 × |MDD_보유|" in 절
    assert v2.RETURN_SLACK_PP == 5.0 and "보유 − 5%p" in 절
    assert (v2.JUDGE_EVAL, v2.REJUDGE_EVAL) == (126, 252) and "252번째 평가에서 같은 기준으로 한 번만" in 절
    assert v2.HOLDOUT_FROM == "2000-01-01" and "2000-01~" in 절
    assert "·".join(th.SECTORS) == "XLB·XLE·XLF·XLI·XLK·XLP·XLU·XLV·XLY" and "·".join(th.SECTORS) in 절
    assert th.LATE_SECTORS == ("XLRE", "XLC") and "XLRE·XLC 는 각자 13개 월말" in 절
    assert th.TOP_K == 3 and "상위 3개" in 절 and (th.FILL, th.INVERSE) == ("SPY", "SH")
    assert th.HARM_PP == -3.0 and "−3%p 미만" in 절
    assert v2.RULE == "etf_tactical_v2" and th.RULE == "etf_theme_v1"


def test_운영_경로는_판2_테마를_부르지_않는다() -> None:
    """꺼진 채 — 12.48 예외 판단의 근거(운영·월 백테스트가 import 하지 않음)"""
    걸림 = [p.relative_to(뿌리).as_posix() for p in (뿌리 / "batch").rglob("*.py")
            if p.stem not in ("etf_tactical_v2", "etf_theme")
            and any(x in p.read_text(encoding="utf-8") for x in ("etf_tactical_v2", "etf_theme"))]  # fmt: skip
    assert 걸림 == []
