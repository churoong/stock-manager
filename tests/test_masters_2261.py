"""거장 부서·거장 계좌 헌장 (docs/company.md 12장, docs/infra.md 25.2261).

손으로 셀 수 있는 고정 자료로 부서마다 판단식을 확인하고, 거장 자료가 붙어도 A·C·D·E 의견이 그대로인지(12.8 불변) 본다.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from batch.core import db
from batch.jobs import company as cjob
from batch.services import company as co
from batch.services import masters as ms
from tests.test_portfolio_job import MemClient


def _연간(*해: tuple[int, float], **공통: float) -> list[dict]:
    """[(사업연도, 순이익)] — 나머지 칸은 공통 값"""
    기본 = {"eq": 1000.0, "ta": 3000.0, "ca": 1000.0, "cl": 400.0, "ncl": 300.0, "oi": 200.0, "ocf": None,
            "cur": "KRW"}  # fmt: skip
    return [{"fy": fy, "ni": ni, **{**기본, **공통}} for fy, ni in 해]


def _d(sid: int, *, annual: list[dict] | None = None, raw: dict | None = None, **m: object) -> co.Dossier:
    return co.Dossier(stock_id=sid, ticker=f"T{sid}", as_of="2026-11-20",
                      masters={"annual": annual or [], "raw": raw or {}, "price_currency": "KRW", **m})  # fmt: skip


# ── 부서 판단식 ────────────────────────────────────────────────────────────────────


def test_버핏부_여섯_항목이_모두_맞으면_찬성() -> None:
    좋음 = _d(1, annual=_연간((2021, 200), (2022, 210), (2023, 220), (2024, 230), (2025, 240), ocf=300.0),
             raw={"profit_stability": 1, "debt_ratio": 0.3, "ep": 0.08}, beta=0.8)  # ROE 20~24%
    나쁨 = _d(2, annual=_연간((2023, 50), (2024, 60), (2025, 70)), raw={"profit_stability": 0, "debt_ratio": 1.5,
                                                                      "ep": 0.02}, beta=1.4)  # fmt: skip
    o = ms.buffett([좋음, 나쁨], [좋음, 나쁨])
    assert (o[1].stance, o[1].conviction) == (1, 1.0)
    # 나쁨: ① ROE 5~7% 불충족 ② ③ ⑤ ⑥ 불충족, ④ 영업현금흐름 없음 → 판정 가능 5 중 0 → 반대
    assert (o[2].stance, o[2].conviction) == (-1, 1.0)


def test_버핏부_판정_가능이_셋_미만이면_자료_없음() -> None:
    d = _d(1, annual=_연간((2025, 100)), raw={})  # ROE 1해(< 3), 나머지 없음
    o = ms.buffett([d], [d])[1]
    assert o.stance == 0 and o.note and o.note.startswith("자료 없음")


def test_린치_비율_문턱() -> None:
    # 순이익 100 → 200 (3년, CAGR 25.99%), PER = 1/0.1 = 10, 배당 2% → R = (25.99 + 2) / 10 = 2.80 ≥ 2 찬성
    d = _d(1, annual=_연간((2022, 100), (2023, 130), (2024, 160), (2025, 200)), raw={"ep": 0.1, "dy": 0.02})
    assert ms.lynch_ratio(d) == pytest.approx(2.7992, abs=1e-3)
    o = ms.lynch([d], [d])[1]
    assert (o.stance, o.conviction) == (1, ms.LYNCH_CONVICTION)
    # 적자 해가 끼면 성장을 내지 않는다
    적자 = _d(2, annual=_연간((2022, -10), (2023, 130), (2024, 160), (2025, 200)), raw={"ep": 0.1})
    assert ms.lynch_ratio(적자) is None
    # 비싸면(PER 50) 반대: R = 26/50 = 0.52 < 1
    비쌈 = _d(3, annual=_연간((2022, 100), (2023, 130), (2024, 160), (2025, 200)), raw={"ep": 0.02})
    assert ms.lynch([비쌈], [비쌈])[3].stance == -1


def test_그레이엄_일곱_항목과_반쪽() -> None:
    해 = _연간((2021, 100), (2022, 110), (2023, 120), (2024, 130), (2025, 140), ca=1000.0, cl=400.0, ncl=300.0)
    # 시총 1500, 3년 평균 이익 130 → PER 11.5 ≤ 15, PBR = 1/0.8 = 1.25 → 14.4 ≤ 22.5
    d = _d(1, annual=해, raw={"cap": 1500.0, "bp": 0.8, "current_ratio": 2.5},
           div={y: True for y in range(2021, 2026)})  # fmt: skip
    항목 = ms.graham_items(d)
    assert [ok for _n, ok, _v, _t in 항목] == [True, True, True, True, True, True, True]
    assert ms.graham([d], [d])[1].stance == 1
    assert (ms.graham_price([d], [d])[1].stance, ms.graham_fin([d], [d])[1].stance) == (1, 1)
    # 배당 한 해 빠짐 → 재무 반쪽은 '모두 충족' 이 아니라 중립(찬성 아님) — 두 반쪽 모두 찬성해야 가결
    빠짐 = replace(d, masters={**d.masters, "div": {y: y != 2023 for y in range(2021, 2026)}})  # type: ignore[dict-item]
    assert ms.graham_fin([빠짐], [빠짐])[1].stance == 0
    # 5 사업연도가 다 없으면 g5·g6 판정 불가 → 재무 반쪽이 찬성할 수 없다
    넷 = replace(d, masters={**d.masters, "annual": 해[1:]})  # type: ignore[dict-item]
    assert ms.graham_fin([넷], [넷])[1].stance != 1


def test_통화가_다르면_시총을_섞는_항목은_판정_불가() -> None:
    d = _d(1, annual=_연간((2023, 100), (2024, 110), (2025, 120), cur="USD"), raw={"cap": 1500.0, "bp": 0.8})
    assert ms.graham_items(d)[0][1] is None and ms.greenblatt_parts(d) == (None, None)


def test_그린블랫_순위와_금융_제외() -> None:
    def 회사(sid: int, oi: float, **m: object) -> co.Dossier:
        return _d(sid, annual=_연간((2025, 100), oi=oi), raw={"cap": 1000.0}, **m)

    pop = [회사(1, 400), 회사(2, 200), 회사(3, 100), 회사(4, 300, financial=True)]
    o = ms.greenblatt(pop, pop)
    assert o[1].stance == 1 and o[3].stance == -1  # 영업이익이 클수록 EY·ROC 둘 다 높다
    assert o[4].note and "금융" in o[4].note


def test_오닐_실적과_주가() -> None:
    d = _d(1, annual=_연간((2022, 100), (2023, 140), (2024, 190), (2025, 260), eq=1200.0),
           raw={"high52": 0.9, "mom12_1": 0.5})  # 3년 연속 증가 · CAGR 37.5% · ROE 21.7%
    pop = [d, *(_d(10 + i, raw={"mom12_1": i / 100}) for i in range(9))]
    assert ms.oneil_earn([d], pop)[1].stance == 1
    assert ms.oneil_price([d], pop)[1].stance == 1
    # 주가부는 두 항목이 다 판정 가능해야 한다(min(3, 2) = 2)
    한쪽 = _d(2, raw={"high52": 0.9})
    assert ms.oneil_price([한쪽], [한쪽])[2].note.startswith("자료 없음")  # type: ignore[union-attr]


def test_린치_재무_점검() -> None:
    pop = [_d(i, annual=_연간((2025, 100), ocf=150.0), raw={"debt_ratio": i / 10}) for i in range(1, 11)]
    o = ms.lynch_check(pop, pop)
    assert o[1].stance == 1 and o[10].stance == -1
    잠식 = _d(11, annual=_연간((2025, 100), eq=-5.0), raw={"debt_ratio": 0.05})
    assert ms.lynch_check([잠식], [*pop, 잠식])[11].stance == -1


# ── 헌장·불변 ───────────────────────────────────────────────────────────────────


def test_거장_헌장은_위원회_규칙에_맞고_부서_이름이_같다() -> None:
    for aid, ch in ms.CHARTERS.items():
        assert co.charter_errors(ch) == [], aid
    assert set(ms.LABELS) == set(co.MASTER_DEPARTMENTS) and ms.DEPTS == co.MASTER_DEPARTMENTS
    assert all(co.DEPT_LABEL[k] == v for k, v in ms.LABELS.items())


def test_거장_자료가_붙어도_A_C_D_E_의견은_그대로다() -> None:
    """12.8 불변 — 거장 갈래는 거장 부서가 가중을 받은 헌장에만 닿는다"""
    pop = [co.Dossier(stock_id=i, ticker=f"T{i}", as_of="2026-11-20", total_score=50 + i,
                      factors={"value": 30 + i, "quality": 60 - i, "growth": 40 + i, "momentum": 20 + 2 * i},
                      mdd=-0.1 * (i % 5 + 1), volatility=0.2 + i / 100, regime="bull", sentiment=float(i * 3 - 20),
                      gov_events=[("2026-01-01", "횡령")] if i == 3 else []) for i in range(1, 15)]  # fmt: skip
    붙임 = [replace(d, masters={"annual": _연간((2025, 100)), "raw": {"ep": 0.1}}) for d in pop]
    for aid, ch in co.CHARTERS.items():
        예전, 지금 = co.meet(pop[:10], ch, pop), co.meet(붙임[:10], ch, 붙임)
        assert [(i.stock_id, i.vote, i.passed, i.reason) for i in 예전[0]] == \
            [(i.stock_id, i.vote, i.passed, i.reason) for i in 지금[0]], aid  # fmt: skip
        assert all(set(i.opinions) & set(co.MASTER_DEPARTMENTS) == set() for i in 지금[0])


def test_조사_자료_행은_거장_자료가_없으면_그대로() -> None:
    d = co.Dossier(stock_id=1, ticker="T1", as_of="2026-11-20")
    assert "masters" not in cjob.dossier_json(d)
    assert "masters" in cjob.dossier_json(replace(d, masters={"annual": [{"fy": 2025}], "raw": {}}))


def test_거장_위원회는_거장_부서로_회의한다() -> None:
    pop = [_d(i, annual=_연간((2021, 200), (2022, 210), (2023, 220), (2024, 230), (2025, 240), ocf=300.0),
              raw={"profit_stability": 1, "debt_ratio": 0.3, "ep": 0.08, "cap": 1500.0, "bp": 0.8,
                   "current_ratio": 2.5}, beta=0.8, div={y: True for y in range(2021, 2026)}) for i in range(1, 4)]  # fmt: skip
    items, _ok = co.meet(pop, ms.CHARTERS["ZM"], pop)
    assert {"buffett", "lynch", "graham", "greenblatt", "oneil_earn", "oneil_price"} <= set(items[0].opinions)


# ── 자료 읽기 (시점) ──────────────────────────────────────────────────────────────


def test_거장_자료는_기준일까지_접수된_것만_읽는다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at, sector_code)"
              " VALUES (1, '000001', 'KOSPI', 'KR', 'KRW', 't', 't', 'KSIC 35')")  # fmt: skip

    def 스냅(fy: int, 접수: str, ni: float, cons: int = 1, rcpt: str = "") -> None:
        c.execute("INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code,"
                  " consolidated, payload, source, fetched_at) VALUES (1, ?, ?, ?, '11011', ?, ?, 't', 't')",
                  [접수, rcpt or f"r{fy}{cons}{접수}", fy, cons,
                   json.dumps({"net_income": ni, "total_equity": 1000, "operating_cash_flow": 50})])  # fmt: skip

    스냅(2023, "2024-03-10", 100)
    스냅(2024, "2025-03-10", 110)
    스냅(2024, "2025-03-10", 999, cons=0)  # 별도 — 연결이 있으니 고르지 않는다
    스냅(2025, "2026-12-01", 500)  # 기준일 뒤 접수 — 미래 정보
    c.execute("INSERT INTO stock_dividends (stock_id, fiscal_year, report_year, receipt_no, as_of_date,"
              " cash_dividend_total, source, fetched_at) VALUES (1, 2024, 2024, 'd', '2025-03-10', 10, 't', 't')")
    c.execute("INSERT INTO stock_dividends (stock_id, fiscal_year, report_year, receipt_no, as_of_date,"
              " cash_dividend_total, source, fetched_at) VALUES (1, 2025, 2025, 'e', '2026-12-01', 10, 't', 't')")
    for 창, 베타 in (("1Y", 1.5), ("3Y", 0.7)):
        c.execute("INSERT INTO performance_metrics (stock_id, as_of_date, window, beta, data_points, calc_version,"
                  " created_at) VALUES (1, '2026-11-19', ?, ?, 100, 1, 't')", [창, 베타])  # fmt: skip
    c.execute("INSERT INTO factors (stock_id, as_of_date, factor, raw_json, peer_group, peer_size, calc_version,"
              " created_at) VALUES (1, '2026-11-20', 'value', ?, 'm', 10, 12, 't')",
              [json.dumps({"ep": 0.1, "_market_cap": {"cap": 1000, "factor": 1.1, "scaled": True}})])  # fmt: skip
    사실 = cjob.master_facts(mem, [1], "KR", "2026-11-20")[1]  # type: ignore[arg-type]
    assert [(r["fy"], r["ni"]) for r in 사실["annual"]] == [(2023, 100), (2024, 110)]
    assert 사실["div"] == {2024: True}
    assert 사실["beta"] == 0.7  # 3Y 먼저
    assert 사실["raw"]["cap"] == pytest.approx(1100.0)  # 옮긴 시총
    assert 사실["utility"] is True and 사실["financial"] is False
    assert all(r["ocf"] is None for r in 사실["annual"])  # 국내 영업현금흐름 채우기 끝 기록이 없다
    db.set_setting(mem, cjob.KR_CFO_DONE_KEY, "2026-11-10")  # type: ignore[arg-type]
    assert cjob.master_facts(mem, [1], "KR", "2026-11-20")[1]["annual"][-1]["ocf"] == 50  # type: ignore[arg-type]


def test_문서_12_3_이_코드_문턱을_적는다() -> None:
    """정의처(docs/company.md 12.3)와 코드 문턱이 같다 — 한쪽만 바꾸면 깨진다"""
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent / "docs" / "company.md").read_text(encoding="utf-8")
    절 = 글[글.index("### 12.3 거장 부서"):글.index("### 12.4 거장 계좌 헌장")]
    for 글자 in (f"≥ {ms.BUFFETT_ROE:.0%}", f"≥ {ms.BUFFETT_MIN_YEARS}", f"베타 ≤ {ms.BUFFETT_BETA:g}",
                 f"`R ≥ {ms.LYNCH_FOR:g}`", f"`R < {ms.LYNCH_AGAINST:g}`", f"확신 {ms.LYNCH_CONVICTION:g} 고정",
                 f"PER ≤ {ms.GRAHAM_PER:g}", f"≤ {ms.GRAHAM_PER_PBR:g}", f"유동비율 ≥ {ms.GRAHAM_CURRENT:g}",
                 f"최근 {ms.GRAHAM_YEARS} 사업연도", f"CAGR ≥ {ms.ONEIL_CAGR:.0%}", f"ROE ≥ {ms.ONEIL_ROE:.0%}",
                 f"≥ {ms.ONEIL_HIGH52:g}", f"백분위 ≥ {ms.ONEIL_RS:g}", f"`p ≤ {ms.LYNCH_CHECK_FOR:g}`",
                 f"`p ≥ {ms.LYNCH_CHECK_AGAINST:g}`"):  # fmt: skip
        assert 글자 in 절, 글자


# ── 25.2263 교차검증 [권고] 보강 ─────────────────────────────────────────────────


def test_버핏부_ROE_문턱_경계가_입장을_바꾼다() -> None:
    """ROE 만 문턱 언저리로 두고 나머지 다섯은 고정 — 비교식(≥)과 ① 의 해 수 조건을 묶는다. 문턱 값 자체는 12.3 대조
    시험(`test_문서_12_3_이_코드_문턱을_적는다`)이 묶는다 (25.2261 교차검증 [권고])"""
    def 회사(sid: int, ni: float, 해수: int = 5) -> co.Dossier:
        return _d(sid, annual=_연간(*((2026 - 해수 + k, ni) for k in range(해수)), ocf=ni + 100),
                  raw={"profit_stability": 1, "debt_ratio": 0.3, "ep": 0.08}, beta=0.8)  # fmt: skip

    딱, 모자람 = 회사(1, 1000 * ms.BUFFETT_ROE), 회사(2, 1000 * ms.BUFFETT_ROE - 1)
    o = ms.buffett([딱, 모자람], [딱, 모자람])
    assert (o[1].stance, o[1].conviction) == (1, 1.0)
    assert o[2].conviction == pytest.approx(round(abs(5 / 6 - 0.5) / 0.5, 6))  # ① 하나만 불충족
    # 관측된 해가 문턱 해 수보다 적으면 ① 은 판정 불가 — ROE 10% 여도 불충족이 아니라 빠져 판정 가능 5 중 5(확신 1)
    둘 = 회사(3, 100, 해수=ms.BUFFETT_MIN_YEARS - 1)
    assert (ms.buffett([둘], [둘])[3].stance, ms.buffett([둘], [둘])[3].conviction) == (1, 1.0)
    assert ms.buffett([회사(4, 100)], [회사(4, 100)])[4].conviction < 1.0  # 해가 차면 ① 불충족


def test_그린블랫부는_유틸리티도_뺀다() -> None:
    d = _d(1, annual=_연간((2025, 100), oi=400.0), raw={"cap": 1000.0}, utility=True)
    assert ms.greenblatt_parts(d) == (None, None)
    assert ms.greenblatt_parts(replace(d, masters={**d.masters, "utility": False})) != (None, None)  # type: ignore[dict-item]


def test_미국_업종_코드로_유틸리티를_가른다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for sid, 코드 in ((1, "SIC 4911"), (2, "SIC 3571")):
        mem.conn.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at, sector_code)"
                         " VALUES (?, ?, 'NYSE', 'US', 'USD', 't', 't', ?)", [sid, f"U{sid}", 코드])  # fmt: skip
    사실 = cjob.master_facts(mem, [1, 2], "US", "2026-11-20")  # type: ignore[arg-type]
    assert (사실[1]["utility"], 사실[2]["utility"]) == (True, False)


def test_같은_날_정정_공시는_접수번호로_고른다() -> None:
    """정정을 먼저 저장해도 접수번호가 큰 정정을 고른다(`backtest._공시_순`, 25.1291) — 배당도 같다"""
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at)"
              " VALUES (1, '000001', 'KOSPI', 'KR', 'KRW', 't', 't')")  # fmt: skip
    for 접수번호, ni in (("20250310000200", 200), ("20250310000100", 100)):  # 정정 먼저 저장
        c.execute("INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code,"
                  " consolidated, payload, source, fetched_at) VALUES (1, '2025-03-10', ?, 2024, '11011', 1, ?, 't', 't')",
                  [접수번호, json.dumps({"net_income": ni})])  # fmt: skip
    for 접수번호, 보고, 금액 in (("20250310000200", 2024, 0), ("20250310000100", 2025, 10)):  # rowid·고유 색인 순서 둘 다에서 원공시가 뒤
        c.execute("INSERT INTO stock_dividends (stock_id, fiscal_year, report_year, receipt_no, as_of_date,"
                  " cash_dividend_total, source, fetched_at) VALUES (1, 2024, ?, ?, '2025-03-10', ?, 't', 't')",
                  [보고, 접수번호, 금액])  # fmt: skip
    사실 = cjob.master_facts(mem, [1], "KR", "2026-11-20")[1]  # type: ignore[arg-type]
    assert [r["ni"] for r in 사실["annual"]] == [200]
    assert 사실["div"] == {2024: False}


def test_팩터_원값은_그날_가장_높은_판을_읽는다() -> None:
    """12.1 덧붙임(25.2263) — 신호(`daily.load_signal_rows`)도 그날 가장 높은 점수 판을 읽는다"""
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at)"
                     " VALUES (1, '000001', 'KOSPI', 'KR', 'KRW', 't', 't')")  # fmt: skip
    for 판, ep in ((12, 0.2), (11, 0.1)):
        mem.conn.execute("INSERT INTO factors (stock_id, as_of_date, factor, raw_json, peer_group, peer_size,"
                         " calc_version, created_at) VALUES (1, '2026-11-20', 'value', ?, 'm', 10, ?, 't')",
                         [json.dumps({"ep": ep}), 판])  # fmt: skip
    assert cjob.master_facts(mem, [1], "KR", "2026-11-20")[1]["raw"]["ep"] == 0.2  # type: ignore[arg-type]


#: docs/company.md 12.4 표 — 사전 등록 값(손으로 옮김). 0096 마이그레이션 시험도 이 값을 쓴다
헌장_12_4 = {
    "ZM": ({"buffett": 1, "lynch": 1, "graham": 1, "greenblatt": 1, "oneil_earn": 0.5, "oneil_price": 0.5,
            "governance": 1, "risk": 0.5}, "buffett", ["governance", "risk"], -0.6, 1.0, 30, 5, "core"),
    "ZN": ({"graham_price": 2, "graham_fin": 2, "governance": 1}, "graham_price", ["governance"], None, 2.0, 50, 5,
           "core"),
    "ZL": ({"lynch": 2, "lynch_check": 2, "governance": 1}, "lynch", ["governance"], None, 2.0, 50, 5, "core"),
}  # fmt: skip


def test_거장_헌장은_사전_등록_12_4_와_같다() -> None:
    for aid, (가중, 주관, 거부, mdd, 문턱, 안건, top_n, 매도) in 헌장_12_4.items():
        ch = ms.CHARTERS[aid]
        assert (ch["weights"], ch["lead"], ch["veto"], ch["max_mdd"], ch["threshold"], ch["agenda"], ch["top_n"],
                ch["sell_rule"]) == (가중, 주관, 거부, mdd, 문턱, 안건, top_n, 매도), aid  # fmt: skip
    assert list(ms.CHARTERS) == list(헌장_12_4)


# ── 조사 준비 (jobs/paper._조사_준비) ───────────────────────────────────────────────


def _준비(monkeypatch: pytest.MonkeyPatch, charters: dict, 실패: bool = False) -> tuple[dict, list]:
    from batch.jobs import paper as jp

    불림: list = []
    d = co.Dossier(stock_id=1, ticker="T1", as_of="2026-11-20")
    monkeypatch.setattr(cjob, "population_dossiers", lambda *a: [d])
    monkeypatch.setattr(cjob, "agenda_rows", lambda rows, n: rows)
    monkeypatch.setattr(cjob, "load_dossiers", lambda *a: [d])

    def 사실(*a: object) -> dict:
        불림.append(a[1])
        if 실패:
            raise TimeoutError("Read timed out")
        return {1: {"annual": [], "raw": {}}}

    monkeypatch.setattr(cjob, "master_facts", 사실)
    cache: dict = {}
    jp._조사_준비(object(), "KR", [], charters, cache, "2026-11-20")
    return cache, 불림


def test_거장_헌장이_없으면_거장_자료를_읽지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    cache, 불림 = _준비(monkeypatch, dict(co.CHARTERS))
    assert 불림 == [] and cache["agenda"][0].masters is None
    assert "masters" not in cjob.dossier_json(cache["agenda"][0])
    # 가중 0 인 거장 키만 있으면 읽지 않는다(`opinions` 와 같은 식)
    cache, 불림 = _준비(monkeypatch, {"A": {**co.CHARTERS["A"], "weights": {**co.CHARTERS["A"]["weights"], "buffett": 0}}})
    assert 불림 == []


def test_거장_헌장이_끼면_모집단과_안건_둘_다에_붙인다(monkeypatch: pytest.MonkeyPatch) -> None:
    cache, 불림 = _준비(monkeypatch, {**co.CHARTERS, **ms.CHARTERS})
    assert 불림 == [[1]] and cache["agenda"][0].masters == cache["population"][0].masters == {"annual": [], "raw": {}}


def test_거장_자료_읽기가_실패하면_거장_계좌만_막는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패해도 캐시는 다 채워진다(A~E 는 그날 회의) — 거장 헌장 계좌는 위원회 실패 (25.2263)"""
    from batch.jobs import paper as jp

    cache, _ = _준비(monkeypatch, {**co.CHARTERS, **ms.CHARTERS}, 실패=True)
    assert "agenda" in cache and "population" in cache and "TimeoutError" in cache["masters_error"]
    assert cache["agenda"][0].masters is None
    assert jp.uses_masters(ms.CHARTERS["ZM"]) and not any(jp.uses_masters(c) for c in co.CHARTERS.values())
    with pytest.raises(RuntimeError, match="거장 자료 읽기 실패"):
        jp.committee_step(object(), market="KR", account="ZM", charter=ms.CHARTERS["ZM"], charters={}, signal_rows=[],
                          trade_date="2026-11-20", cache=cache, now="t", rule_version=1)  # fmt: skip


def test_한도_초과는_삼키지_않고_배치를_멈춘다(monkeypatch: pytest.MonkeyPatch) -> None:
    """거장 읽기 실패를 거장 계좌만 막는 것은 고장일 때만 — 한도는 다른 자리처럼 올린다 (25.2264, 교차검증 [권고])"""
    from batch.jobs import paper as jp

    d = co.Dossier(stock_id=1, ticker="T1", as_of="2026-11-20")
    monkeypatch.setattr(cjob, "population_dossiers", lambda *a: [d])
    monkeypatch.setattr(cjob, "agenda_rows", lambda rows, n: rows)
    monkeypatch.setattr(cjob, "load_dossiers", lambda *a: [d])

    def 한도(*a: object) -> dict:
        raise RuntimeError("D1_ERROR: Exceeded row read limit")

    monkeypatch.setattr(cjob, "master_facts", 한도)
    cache: dict = {}
    with pytest.raises(RuntimeError, match="row read limit"):
        jp._조사_준비(object(), "KR", [], {**co.CHARTERS, **ms.CHARTERS}, cache, "2026-11-20")
    assert "masters_error" not in cache


def test_가장_늦은_해에_별도만_있으면_별도로_고른다() -> None:
    """`_pit_annual` 의 반대 방향 — 옛 해에 연결이 있어도 끝해가 별도뿐이면 별도로 일관되게 (backtest 규칙)"""
    줄 = [
        {"fiscal_year": 2023, "consolidated": 1, "as_of_date": "2024-03-10", "receipt_no": "a", "v": "연결23"},
        {"fiscal_year": 2023, "consolidated": 0, "as_of_date": "2024-03-10", "receipt_no": "b", "v": "별도23"},
        {"fiscal_year": 2024, "consolidated": 0, "as_of_date": "2025-03-10", "receipt_no": "c", "v": "별도24"},
    ]
    assert [r["v"] for r in cjob._pit_annual(줄)] == ["별도23", "별도24"]
    assert [r["v"] for r in cjob._pit_annual(줄[2:])] == ["별도24"]
