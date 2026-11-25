"""매수 단계가 이상으로 일찍 끝난 날(환율 없음·미체결 달러 환율 모름·장부 현금 음수)을 운용 점검·관문 무사고가 센다
(docs/infra.md 25.2226, 11-12 결함 회차 [권고]).

예전엔 `desk.account_skips` 가 buy 칸을 보지 않고 `checks` 에도 줄이 없어, H 가 장부 현금 음수로 사지 못해도
'확인할 일: 없음' 이고 관문 '운영 무사고' 는 사고 0일이었다."""

from __future__ import annotations

import inspect
from pathlib import Path

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
    # 예전의 이른 반환 거름·`list()` 검사(25.2333)는 아래 허용 꼴이 대신한다(25.2338 에서 지움)
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


#: `skipped` 열쇠에 실리는 글 가운데 **매수 이상이 아닌 것**(정상 쉼·단계 수준 — 단계 수준 판정은 `desk.paper_step_failed`
#: 몫이라 "사고 아님" 이라는 뜻이 아니다) — 모듈 전체 대조의 반대쪽(25.2338). `jobs/paper_etf` 는 대조 밖이다 — ETF 기록은
#: `desk._buy_fault` 가 `etf` 칸으로 빼고 `_etf_warning` 이 따로 띄운다. 새 글을 쓰면
#: `pp.BUY_FAULT_SKIPS`(이상 — 운용 점검이 띄움) 또는 여기(정상 — 사유와 함께) 가운데 한 곳에 넣어야 시험이 지나간다
NORMAL_SKIPS: dict[str, str] = {
    "배분하지 않는 날(신호 없음·묵음·추천 못 읽음)": "배분 계좌가 쉬는 날 — 신호 없음은 리포트가 따로 말한다(jobs/paper run)",
    "그 달 첫 거래일 장이 아님": "Z3 은 한 달에 한 번만 산다(paper.md 18장)",
    "입금 없음": "단계 수준 — 장부가 빈 날(계좌 buy 기록이 아니다)",
    "다른 시장의 가상모의투자 단계가 도는 중(잠금)": "단계 수준 — 잠금 건너뜀은 경고 줄과 `(잠금)` 끝말로 따로 띄운다(25.2145)",
}
#: 글자 상수가 아닌 `skipped` 값 — 고정된 글자 그대로만(정상 쉼의 f-string · 후보별 목록)
NORMAL_SKIP_FORMS = frozenset({
    "빠짐[:10]",
    "f'보유 {len(held)} — 상한 {상한}'",
    "f'시장 국면 {trend.STATE_LABEL.get(str(상태), '모름')}({지수}) — 강세일 때만'",
})


def _첨자_자리_됨(첨자: object, 부모: dict) -> bool:
    """`x["skipped"]` 첨자가 놓여도 되는 자리 — 읽기(Load), 또는 대입문의 **곧은** 과녁(25.2340, 25.2339 교차검증 [권고]:
    튜플 풀기 `x["skipped"], _ = "새", 0`·`for x["skipped"] in …` 과녁은 자리 검사도 값 모으기도 빠져나갔다). 지우기(Del)·
    튜플·for·with 과녁은 실패"""
    import ast

    assert isinstance(첨자, ast.Subscript)
    if isinstance(첨자.ctx, ast.Load):
        return True
    위 = 부모.get(첨자)
    if isinstance(위, ast.Assign):
        return any(t is 첨자 for t in 위.targets)
    return isinstance(위, ast.AugAssign | ast.AnnAssign) and 위.target is 첨자


def test_batch_에_skipped_이름_상수가_없다() -> None:
    """이름 열쇠(`{pp.SKIP_KEY: "새"}`)는 두 모듈의 글자 `"skipped"` 만 보는 허용 목록 밖이다(25.2340, 25.2339 교차검증
    [참고]) — 값이 `"skipped"` 인 대입이 batch 어디에도 없게 묶는다"""
    import ast

    뿌리 = Path(__file__).resolve().parent.parent / "batch"
    for 파일 in 뿌리.rglob("*.py"):
        for n in ast.walk(ast.parse(파일.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Assign | ast.AnnAssign) and isinstance(n.value, ast.Constant):
                assert n.value.value != "skipped", f"{파일}:{n.lineno}"


def test_모듈_전체의_skipped_글은_이상이나_정상_한쪽에_든다() -> None:
    """25.2338 (25.2337 교차검증 [권고]) — 두 함수의 return 만 보아, 부르는 쪽(`run` 의 `a기록["buy"] = {"skipped": …}`
    직접 대입·`paper_solo.plan`)에 새 이상 까닭을 넣고 `BUY_FAULT_SKIPS` 에 안 넣으면 `desk._buy_fault` 가 정상 쉼으로
    치는데 아무 시험도 깨지지 않았다. 두 모듈 전체에서 `skipped` 를 쓰는 자리(dict 글자·`x["skipped"] =` 대입·
    `skipped=` 키워드)를 모두 모아 한쪽에 들었는지 견준다"""
    import ast

    from batch.jobs import paper_solo

    본것: set[str] = set()
    assert not set(NORMAL_SKIPS) & set(pp.BUY_FAULT_SKIPS)  # 한쪽에만(25.2339)
    for 모듈 in (job, paper_solo):
        나무 = ast.parse(inspect.getsource(모듈))
        # `"skipped"` 글자가 놓일 수 있는 자리를 **허용 목록**으로(25.2339, 25.2338 교차검증 [권고] — 25.2338 은 쓰는 꼴 셋만
        # 모아 `.setdefault("skipped", …)`·`dict([("skipped", …)])`·`SKIP_KEY = "skipped"; {SKIP_KEY: …}` 가 빠졌다):
        # dict 열쇠 · 첨자(`x["skipped"]` 읽기·쓰기) · `.get("skipped")` 첫 인자. 그 밖 자리면 실패 — 아래 세 꼴이 모으는
        # 쓰는 자리 전부가 된다
        부모 = {c: n for n in ast.walk(나무) for c in ast.iter_child_nodes(n)}
        for c in ast.walk(나무):
            if not (isinstance(c, ast.Constant) and c.value == "skipped"):
                continue
            위 = 부모.get(c)
            허용 = ((isinstance(위, ast.Dict) and any(k is c for k in 위.keys))
                  or (isinstance(위, ast.Subscript) and 위.slice is c and _첨자_자리_됨(위, 부모))
                  or (isinstance(위, ast.Call) and isinstance(위.func, ast.Attribute) and 위.func.attr == "get"
                      and 위.args[:1] == [c]))  # fmt: skip
            assert 허용, (모듈.__name__, c.lineno, ast.unparse(위) if 위 is not None else None)
        for n in ast.walk(나무):
            값들: list[ast.expr] = []
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values, strict=True):
                    if isinstance(k, ast.Constant) and k.value == "skipped":
                        값들.append(v)
            elif isinstance(n, ast.Assign | ast.AugAssign | ast.AnnAssign):
                과녁 = n.targets if isinstance(n, ast.Assign) else [n.target]
                for t in 과녁:
                    if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and t.slice.value == "skipped":
                        assert n.value is not None
                        값들.append(n.value)
            elif isinstance(n, ast.Call):
                값들 += [kw.value for kw in n.keywords if kw.arg == "skipped"]
            for v in 값들:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    assert v.value in pp.BUY_FAULT_SKIPS or v.value in NORMAL_SKIPS, (모듈.__name__, v.value)
                    본것.add(v.value)
                else:
                    # 꼴은 다시 파싱한 나무로 견준다(25.2339 — unparse 글자는 따옴표 꼴이 파이썬 판마다 다를 수 있다)
                    꼴 = next((f for f in NORMAL_SKIP_FORMS if ast.dump(ast.parse(f, mode="eval").body) == ast.dump(v)),
                             None)  # fmt: skip
                    assert 꼴 is not None, (모듈.__name__, ast.unparse(v))
                    본것.add(꼴)
    # 목록이 낡지 않게 — 정상 목록의 글·꼴이 모두 실제로 쓰인다
    assert set(NORMAL_SKIPS) | NORMAL_SKIP_FORMS <= 본것, (set(NORMAL_SKIPS) | NORMAL_SKIP_FORMS) - 본것
