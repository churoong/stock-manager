"""매수 단계가 이상으로 일찍 끝난 날(환율 없음·미체결 달러 환율 모름·장부 현금 음수)을 운용 점검·관문 무사고가 센다
(docs/infra.md 25.2226, 11-12 결함 회차 [권고]).

예전엔 `desk.account_skips` 가 buy 칸을 보지 않고 `checks` 에도 줄이 없어, H 가 장부 현금 음수로 사지 못해도
'확인할 일: 없음' 이고 관문 '운영 무사고' 는 사고 0일이었다."""

from __future__ import annotations

import inspect

import pytest

from batch.jobs import paper as job
from batch.services import desk
from batch.services import paper as pp
from tests.test_gate_account_skips_2142 import _읽기


@pytest.mark.parametrize(
    ("계좌", "사고"),
    [
        ({"H": {"recon_ok": True, "holding": {}, "buy": {"skipped": "장부 현금이 음수"}}}, True),
        ({"A": {"buy": {"skipped": "환율 없음"}}}, True),
        ({"B": {"buy": {"skipped": "미체결 달러 주문의 환율 모름"}}}, True),
        ({"H": {"buy": {"skipped": "배분하지 않는 날(신호 없음·묵음·추천 못 읽음)"}}}, False),  # 정상 쉼
        ({"A": {"buy": {"candidates": 3, "skipped": ["종목 상한"]}}}, False),  # 후보별 건너뜀(목록)
        ({"X": {"etf": {"regime": "up"}, "buy": {"skipped": "환율 없음"}}}, False),  # ETF 는 제 경고
    ],
)
def test_이상_끝남은_사고_정상_쉼은_아님(계좌: dict, 사고: bool) -> None:
    모음 = desk.account_skips(계좌)
    assert bool(모음) is 사고, 모음
    log = {"paper": {"accounts": 계좌, **({"account_skips": 모음} if 모음 else {})}}
    (줄,) = _읽기([log])
    assert desk.batch_incident_days([줄], {"daily_kr": ["2026-10-10"]}) == (1 if 사고 else 0)


def test_확인할_일에_줄이_서고_장부_현금_음수는_높음() -> None:
    p = {"accounts": {"H": {"recon_ok": True, "buy": {"skipped": "장부 현금이 음수"}},
                      "A": {"buy": {"skipped": "환율 없음"}}}}  # fmt: skip
    줄 = desk.checks(halted=False, paper=p, broker=None, recon_bad=[], warnings=0)
    글 = {x["text"]: x["level"] for x in 줄 if "매수 단계 건너뜀" in x["text"]}
    assert len(글) == 2
    assert [lv for t, lv in 글.items() if "장부 현금이 음수" in t] == ["high"]
    assert [lv for t, lv in 글.items() if "환율 없음" in t] == ["normal"]


def test_까닭_글이_buy_orders_와_같다() -> None:
    """거장 단독 Z3·Z4 의 `paper_solo.size_orders` 도 같은 글이어야 한다(25.2330, 25.2329 교차검증 [참고] — 글자가
    어긋나면 미국 실행의 환율 없음 날이 `desk._buy_fault` 에 걸리지 않고, `solo.warning` 은 skipped 면 None 이라
    아무 줄도 뜨지 않는다)"""
    from batch.jobs import paper_solo

    for 함수 in (job.buy_orders, paper_solo.size_orders):
        src = inspect.getsource(함수)
        for 까닭 in pp.BUY_FAULT_SKIPS:
            assert f'{{"skipped": "{까닭}"}}' in src, (함수.__name__, 까닭)


def test_같은_까닭은_한_줄로_모은다() -> None:
    """미국 실행 환율 없음 — 배분 계좌가 모두 같은 날 멈춘다. 계좌마다 줄을 세우면 텔레그램 세 줄이 밀렸다(25.2227)"""
    p = {"accounts": {aid: {"buy": {"skipped": "환율 없음"}} for aid in ("A", "B", "C", "D", "E", "G", "H")}}
    p["accounts"]["X"] = {"etf": {"regime": "up"}, "buy": {"skipped": "환율 없음"}}  # ETF 는 빠진다
    줄 = [x for x in desk.checks(halted=False, paper=p, broker=None, recon_bad=[], warnings=0)
         if "매수 단계 건너뜀" in x["text"]]  # fmt: skip
    assert len(줄) == 1 and 줄[0]["level"] == "normal"
    assert desk._이름("X") not in 줄[0]["text"] and desk._이름("H") in 줄[0]["text"]


def test_buy_orders_의_글자_까닭은_모두_상수에_있다() -> None:
    """반대 방향 — 새 이른 반환 글이 생기고 상수에 안 넣으면 조용히 정상 쉼이 된다(25.2227). 거장 단독
    `paper_solo.size_orders` 도(25.2331, 25.2330 교차검증 [권고] — 단독은 `solo.warning` 이 skipped 면 None 이라 상수에 없는
    새 이른 반환은 어디에도 줄이 뜨지 않는다)"""
    from batch.jobs import paper_solo

    for 함수 in (job.buy_orders, paper_solo.size_orders):
        글들 = _skipped_글들(함수)
        assert 글들 == set(pp.BUY_FAULT_SKIPS), 함수.__name__


#: 정상 반환이 `skipped` 에 싣는 후보별 건너뜀 목록의 꼴(이상 까닭이 아니다) — 이 글자 그대로만 상수가 아니어도 된다
SKIPPED_LIST_FORMS = frozenset({"빠짐[:10]"})
#: 끝의 정상 반환이 dict 에 펼치는 꼴(`paper_solo.size_orders` 의 못 산 후보 칸) — 이 글자 그대로만 펼침을 허용한다
SPREAD_FORMS = frozenset({"{'not_bought': 못삼} if 못삼 else {}"})


def _skipped_글들(함수: object) -> set[str]:
    """함수의 return 값 안에 있는 `"skipped"` 열쇠 dict 의 값 — AST 로(25.2332, 25.2331 교차검증 [권고]: 정규식은 한 줄짜리
    `return [], {"skipped": "글"}` 꼴만 잡아, 열쇠를 더 붙이거나 f-string·여러 줄로 쓴 새 까닭은 조용히 빠졌다). 값이 글자
    상수가 아니면(f-string·이름) 실패 — 상수와 견줄 수 없는 까닭은 `desk._buy_fault` 가 못 띄운다"""
    import ast
    import textwrap

    나무 = ast.parse(textwrap.dedent(inspect.getsource(함수)))  # type: ignore[arg-type]
    글들: set[str] = set()
    # 꼴을 강제한다(25.2333, 25.2332 교차검증 [권고]) — 이른 반환의 둘째 칸이 dict 글자가 아니면(`return [], 기록`·
    # `dict(skipped=…)`) 대조가 까닭을 볼 수 없어 실패시킨다. 빈 목록을 호출로 만든 꼴(`list()`)도 쓰지 않는다.
    # 첫 칸을 변수에 담은 꼴은 아래 글 모으기가 덮는다(25.2335·25.2336)
    for n in ast.walk(나무):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple) and n.value.elts:
            첫 = n.value.elts[0]
            assert not (isinstance(첫, ast.Call) and ast.unparse(첫.func) == "list"), ast.unparse(n)
    # **허용하는 꼴 하나**(25.2337, 25.2336 교차검증 [권고]) — 25.2332→2333→2335→2336 이 거를 꼴을 하나씩 더했는데 매번
    # 거름 밖 꼴(튜플을 변수에 담은 반환·`{**기록}` 펼침·이름 열쇠·도우미 호출 반환·첫 칸 변수 + 목록 꼴)이 다시 나왔다.
    # 이제 거꾸로 묶는다: 함수 안의 **모든** return 은 `(…, {글자 열쇠: …})` 두 칸 튜플이고, 그 dict(안쪽 포함)의 열쇠는 모두
    # 글자 상수, `skipped` 값은 글자 상수. 예외는 함수 본문의 **마지막 문장**인 정상 반환의 후보별 목록 꼴
    # `SKIPPED_LIST_FORMS`·펼침 꼴 `SPREAD_FORMS` 뿐(글자 그대로)
    본문 = 나무.body[0].body  # type: ignore[attr-defined]
    끝반환 = 본문[-1] if isinstance(본문[-1], ast.Return) else None
    for 반환 in (n for n in ast.walk(나무) if isinstance(n, ast.Return)):
        꼴 = 반환.value
        assert isinstance(꼴, ast.Tuple) and len(꼴.elts) == 2 and isinstance(꼴.elts[1], ast.Dict), ast.unparse(반환)
        for d in (n for n in ast.walk(꼴) if isinstance(n, ast.Dict)):
            for k, v in zip(d.keys, d.values, strict=True):
                if k is None:  # `**` 펼침 — 끝반환의 고정된 꼴만(`SPREAD_FORMS`)
                    assert 반환 is 끝반환 and ast.unparse(v) in SPREAD_FORMS, ast.unparse(d)
                    continue
                assert isinstance(k, ast.Constant) and isinstance(k.value, str), ast.unparse(d)
                if k.value != "skipped":
                    continue
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    글들.add(v.value)
                else:
                    assert 반환 is 끝반환 and ast.unparse(v) in SKIPPED_LIST_FORMS, ast.unparse(v)
    return 글들


def test_같은_날_다시_돌아_샀어도_사고로_남는다() -> None:
    """보수 쪽 — 25.2145 의 '다시 돌아도 사고' 가 매수 이상 끝남에도 붙는다(25.2227 에 적음)"""
    계좌 = {"H": {"buy": {"skipped": "환율 없음"}}}
    줄들 = _읽기([{"paper": {"accounts": 계좌, "account_skips": desk.account_skips(계좌)}},
                {"paper": {"accounts": {"H": {"buy": {"orders": 2}}}, "account_skips": {}}}])
    for r in 줄들:
        r["trade_date"] = "2026-10-10"
    assert desk.batch_incident_days(줄들, {"daily_kr": ["2026-10-10"]}) == 1
