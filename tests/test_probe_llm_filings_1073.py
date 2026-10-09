"""공시 읽기 소형 모델 시험의 원문 처리 (docs/infra.md 25.1073).

모델 답을 채점하는 잣대(절 찾기·창·근거 일치)가 틀리면 시험 수치 자체가 거짓이 된다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "probe_llm_filings", Path(__file__).resolve().parent.parent / "scripts" / "probe_llm_filings.py"
)
assert _spec and _spec.loader
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

XML = """<DOCUMENT><SECTION-1><TITLE ATOC="Y">II. 사업의 내용</TITLE>
<SECTION-2><TITLE ATOC="Y">3. 원재료 및 생산설비</TITLE><P>주요 매입처는 &amp;Foo 와 바(주)이다.</P>
<SECTION-3><TITLE>가. 원재료</TITLE><TABLE><TR><TD>품목</TD><TD>매입처</TD></TR>
<TR><TD>웨이퍼</TD><TD>SK실트론</TD></TR></TABLE></SECTION-3></SECTION-2>
<SECTION-2><TITLE ATOC="Y">4. 매출 및 수주상황</TITLE><P>주요 매출처는 Apple, Verizon 등이다.</P>
<P>매출 실적은 아래와 같다.</P></SECTION-2>
<SECTION-2><TITLE ATOC="Y">5. 위험관리 및 파생거래</TITLE><P>주요 고객 아님</P></SECTION-2>
</SECTION-1></DOCUMENT>"""


def test_절은_하위_절까지_통째로_다음_절_앞에서_끊는다() -> None:
    s = probe.sections(XML)
    assert set(s) == {"원재료", "매출 및 수주"}
    assert "SK실트론" in s["원재료"] and "웨이퍼 | SK실트론" in s["원재료"]  # 하위 절 표까지
    assert "&Foo" in s["원재료"]  # 엔티티를 푼다
    assert "Apple" in s["매출 및 수주"] and "위험관리" not in s["매출 및 수주"]


def test_창은_거래처_낱말_줄과_앞뒤만() -> None:
    text = "\n".join(["가", "나", "주요 매출처는 A", "라", "마", "바", "사"])
    assert probe.window(text) == "나\n주요 매출처는 A\n라"
    assert len(probe.window("고객\n" * 5000, limit=100)) == 100


def test_근거_일치는_띄어쓰기_표칸을_무시하고_지어낸_것은_잡는다() -> None:
    src = probe.sections(XML)["매출 및 수주"]
    assert probe.grounded({"name": "Apple", "evidence": "주요 매출처는 Apple,Verizon 등이다"}, src) == (True, True)
    assert probe.grounded({"name": "Samsung", "evidence": "주요 매출처는 Samsung"}, src) == (False, False)
    assert probe.grounded({"name": "verizon", "evidence": ""}, src) == (True, False)
