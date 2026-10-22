"""37회차 후보 B — 실적 발표 반응 사건 분류·EAR·값·문턱 (docs/factors.md 12.40 B·B′, docs/infra.md 25.1658).

손으로 셀 수 있는 공시 순서로 사건·중복·모름 세 갈래와 미래 정보 차단(뒤 보고서·뒤 공시를 바꿔도 앞 분류가 같다)을 본다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from batch.services import earnings_events as ee


def _평일(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _덮음(a: date, b: date) -> bool:
    return True


def _분류(보고서: list[tuple[int, str, date]], 잠정: list[date], 덮음=_덮음, 결산월: int | None = 12) -> list[tuple]:
    return [(c.day, c.source, c.kind) for c in ee.classify_events(ee.first_receipts(보고서), 잠정, 덮음, 결산월, _평일)]


# 2025 사업보고서(2026-03-10) → 2026 1분기 잠정(04-28) → 1분기 보고서(05-14) → 반기 잠정(07-25) → 반기 보고서(08-13)
기본 = [(2025, "11014", date(2025, 11, 13)), (2025, "11011", date(2026, 3, 10)), (2026, "11013", date(2026, 5, 14)),
       (2026, "11012", date(2026, 8, 13))]  # fmt: skip


def test_세번째_경계부터_보고서가_사건이_될_수_있다() -> None:
    셋 = [(2025, "11012", date(2025, 8, 13)), *기본]
    got = dict(((d, s), k) for d, s, k in _분류(셋, []))
    assert got[(date(2025, 11, 13), "보고서")] == "모름"  # 반기를 믿지 못함(그 앞 1분기 없음)
    assert got[(date(2026, 3, 10), "보고서")] == "사건"  # 3분기·사업 둘 다 믿음, 사이에 잠정 없음


def test_잠정이_사건이고_뒤_보고서는_중복() -> None:
    got = _분류(기본, [date(2026, 4, 28), date(2026, 7, 25)])
    assert got == [
        (date(2025, 11, 13), "보고서", "모름"),  # 첫 보고서 — 바로 앞 보고서 경계가 없어 믿지 못함
        (date(2026, 3, 10), "보고서", "모름"),  # 사업보고서는 믿지만 앞 경계(3분기)를 믿지 못함 — ④ 는 둘 다 믿어야
        (date(2026, 4, 28), "잠정", "사건"),
        (date(2026, 5, 14), "보고서", "중복"),  # 같은 구간 잠정이 먼저 — 앞 사건 값 유지
        (date(2026, 7, 25), "잠정", "사건"),
        (date(2026, 8, 13), "보고서", "중복"),
    ]


def test_같은_날_잠정과_보고서는_잠정이_먼저() -> None:
    got = _분류(기본, [date(2026, 5, 14)])
    assert (date(2026, 5, 14), "잠정", "사건") in got and (date(2026, 5, 14), "보고서", "중복") in got


def test_정정_행은_경계가_아니다() -> None:
    정정 = [*기본, (2026, "11013", date(2026, 6, 20))]  # 1분기 정정 — 처음 접수일만 경계
    assert _분류(정정, [date(2026, 7, 25)]) == _분류(기본, [date(2026, 7, 25)])


def test_기한_뒤_처음_접수는_믿지_않고_그_뒤_구간이_모름() -> None:
    늦음 = [(2025, "11014", date(2025, 11, 13)), (2025, "11011", date(2026, 3, 10)),
            (2026, "11013", date(2026, 6, 30))]  # 1분기 기한 05-15 뒤 — 정정본만 남은 백필  # fmt: skip
    got = dict(((d, s), k) for d, s, k in _분류(늦음, [date(2026, 7, 25)]))
    assert got[(date(2026, 6, 30), "보고서")] == "모름"  # 믿지 못하고 앞 잠정도 없다
    assert got[(date(2026, 7, 25), "잠정")] == "모름"  # 앞 경계를 믿지 못함


def test_기한_말일이_휴일이면_다음_거래일까지_인정() -> None:
    # 2026 3분기 기한 = 09-30 + 45 = 11-14(토) → 11-16(월). 11-16 접수는 믿는다
    보고서 = [(2026, "11012", date(2026, 8, 13)), (2026, "11014", date(2026, 11, 16))]
    assert ee.deadline(2026, "11014", 12, _평일) == date(2026, 11, 16)
    assert _분류(보고서, [])[-1][2] == "모름"  # 반기 앞 1분기 경계가 없어 반기를 못 믿음 → 3분기 앞 경계 못 믿음
    보고서 = [(2026, "11013", date(2026, 5, 14)), *보고서]
    assert _분류(보고서, [])[-1] == (date(2026, 11, 16), "보고서", "사건")
    늦게 = [(2026, "11013", date(2026, 5, 14)), (2026, "11012", date(2026, 8, 13)), (2026, "11014", date(2026, 11, 17))]
    assert _분류(늦게, [])[-1][2] == "모름"


def test_다음_보고서_기한이_지났는데_경계가_없으면_모름() -> None:
    # 1분기(05-14) 뒤 반기 보고서가 빠졌다 — 반기 기한 08-14 뒤의 3분기 잠정은 두 회계 기간에 걸친다
    빠짐 = [(2025, "11014", date(2025, 11, 13)), (2025, "11011", date(2026, 3, 10)), (2026, "11013", date(2026, 5, 14))]
    got = _분류(빠짐, [date(2026, 7, 25), date(2026, 10, 28)])
    assert (date(2026, 7, 25), "잠정", "사건") in got and (date(2026, 10, 28), "잠정", "모름") in got


def test_덮지_못한_구간은_모름_결산월_모르면_모름() -> None:
    def 구멍(a: date, b: date) -> bool:
        return not (a < date(2026, 4, 1) <= b)

    got = dict(((d, s), k) for d, s, k in _분류(기본, [date(2026, 4, 28)], 구멍))
    assert got[(date(2026, 4, 28), "잠정")] == "모름"
    # 결산월을 확인 못 하면(또는 12월 결산이 아니면) 어떤 경계도 믿지 않아 사건이 하나도 없다
    # (못 믿는 보고서 앞에 실적 공시가 있으면 "중복" — 그 공시가 이미 모름이라 값은 NULL 그대로)
    assert "사건" not in {k for _d, _s, k in _분류(기본, [date(2026, 4, 28)], 결산월=None)}
    assert "사건" not in {k for _d, _s, k in _분류(기본, [date(2026, 4, 28)], 결산월=3)}


def test_뒤_자료를_바꿔도_앞_분류는_같다() -> None:
    """미끼: 뒤에 올 보고서·공시를 지우거나 더해도 그 전 항목의 분류가 바뀌지 않는다(앞쪽 경계만 본다)."""
    앞 = _분류(기본[:3], [date(2026, 4, 28)])
    뒤까지 = _분류(기본, [date(2026, 4, 28), date(2026, 7, 25), date(2026, 9, 1)])
    assert 뒤까지[: len(앞)] == 앞
    # 양성 대조 — 앞쪽 자료를 바꾸면 바뀐다
    assert _분류(기본[:3], [date(2026, 4, 1), date(2026, 4, 28)])[2:] != 앞[2:]


def test_제목_분류() -> None:
    assert ee.is_result_title("연결재무제표기준영업(잠정)실적(공정공시)")
    assert ee.is_result_title("매출액또는손익구조30%(대규모법인은15%)이상변경")
    assert not ee.is_result_title("[기재정정]연결재무제표기준영업(잠정)실적(공정공시)")
    assert not ee.is_result_title("영업(잠정)실적(전망)")
    assert not ee.is_result_title("결산실적공시예고")


def _축(n: int) -> list[str]:
    out, d = [], date(2026, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_EAR_손계산과_빈칸() -> None:
    축 = _축(10)
    종가 = {d: 100.0 for d in 축} | {축[6]: 110.0}  # D = 축[5], D−2 = 축[3], D+1 = 축[6]
    지수 = {d: 1000.0 for d in 축} | {축[6]: 1020.0}
    assert ee.ear(축, 축[5], 종가, 지수) == pytest.approx(0.10 - 0.02)
    assert ee.ear(축, 축[5], {k: v for k, v in 종가.items() if k != 축[3]}, 지수) is None  # 정지
    assert ee.ear(축, 축[1], 종가, 지수) is None and ee.ear(축, 축[9], 종가, 지수) is None  # 창 밖
    계수 = {d: 1.0 for d in 축} | {축[6]: 2.0}  # 창 안 분할
    assert ee.ear(축, 축[5], 종가, 지수, 계수) is None
    assert ee.ear(축, 축[5], 종가, 지수, {d: 1.0 for d in 축}) == pytest.approx(0.08)


def test_값은_D다음날부터_보이고_중복은_유지_모름은_끊고_묵으면_없음() -> None:
    축 = _축(200)
    덮 = lambda a, b: True  # noqa: E731
    사건 = [(축[10], "사건", 0.05), (축[30], "중복", None), (축[40], "모름", None), (축[50], "사건", 0.02)]
    assert ee.value_at(축, 축[10], 사건, 덮) is None  # 사건일 당일은 아직 모른다
    assert ee.value_at(축, 축[11], 사건[:1], 덮) is None  # 63거래일 창이 차기 전(k < 63)
    assert ee.value_at(축, 축[70], 사건[:2], 덮) == pytest.approx(0.05)  # 중복은 앞 값 유지
    assert ee.value_at(축, 축[70], 사건[:3], 덮) is None  # 모름이 끊는다
    assert ee.value_at(축, 축[70], 사건, 덮) == pytest.approx(0.02)
    assert ee.value_at(축, 축[51 + 63], 사건, 덮) == pytest.approx(0.02)
    assert ee.value_at(축, 축[51 + 64], 사건, 덮) is None  # 63거래일보다 묵음
    assert ee.value_at(축, 축[70], 사건, lambda a, b: False) is None  # 창을 덮지 못함


def test_매수_문턱은_252일이_차야_하고_최근_순위_90분위() -> None:
    축 = _축(400)
    알려진 = [(축[300 + i], float(i)) for i in range(10)]  # 0..9
    assert ee.top_cut(축, 축[250], 알려진, 축[0]) is None  # 252일이 안 참
    assert ee.top_cut(축, 축[350], 알려진, 축[0]) == 8.0  # ⌈0.9·10⌉ = 9번째 = 8
    assert ee.top_cut(축, 축[350], 알려진 + [(축[351], 99.0)], 축[0]) == 8.0  # 기준일 뒤 것은 안 본다
    assert ee.top_cut(축, 축[399], [(축[100], 5.0)], 축[0]) is None  # 창 밖뿐


def test_같은_구간_두_번째_잠정은_중복() -> None:
    """삼성전자처럼 잠정을 두 번(잠정 → 확정 잠정) 내도 구간 첫 사건 하나만."""
    got = _분류(기본, [date(2026, 4, 7), date(2026, 4, 28)])
    assert (date(2026, 4, 7), "잠정", "사건") in got and (date(2026, 4, 28), "잠정", "중복") in got
