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


def test_모델_답_파싱과_핑_채점(monkeypatch) -> None:  # noqa: ANN001
    """백엔드마다 답 모양이 다르다 — 생각 꼬리표·코드펜스를 걷어 내고, 핑 정답은 고객·공급이 정확히 맞을 때만 (25.1074)."""
    assert probe.parse_answer('<think>음</think>```json\n{"customers": []}\n```') == {"customers": []}
    assert probe.parse_answer("HTTP 403: forbidden") is None and probe.parse_answer("[1, 2]") is None
    좋음 = {"customers": [{"name": "Apple"}, {"name": "verizon"}], "suppliers": [{"name": "SK 실트론"}],
          "anonymous": ["A사"]}  # fmt: skip
    assert probe.ping_score(좋음).startswith("정답")
    지어냄 = {**좋음, "customers": [*좋음["customers"], {"name": "한빛전자(주)"}]}  # 종속회사를 고객으로
    assert probe.ping_score(지어냄).startswith("오답") and probe.ping_score(None) == "JSON 아님"
    # 원격 백엔드는 토큰을 요청 머리로만 쓰고, 실패 답에는 본문만 남긴다
    보낸것 = {}

    class 응답:
        status_code = 401
        text = "unauthorized"

    def 가짜_post(url, **kw):  # noqa: ANN001, ANN003, ANN202
        보낸것.update(url=url, **kw)
        return 응답()

    monkeypatch.setattr(probe.requests, "post", 가짜_post)
    monkeypatch.setenv("D1_ACCOUNT_ID", "acct")
    monkeypatch.setenv("D1_API_TOKEN", "비밀")
    complete, 이름 = probe.remote_backend("cloudflare", None)
    raw, _ = complete("p")
    assert "/accounts/acct/" in 보낸것["url"] and 보낸것["headers"]["Authorization"] == "Bearer 비밀"
    assert raw.startswith("HTTP 401") and "비밀" not in raw and 이름.startswith("cloudflare:")


def test_원격_답이_JSON_아니면_멈추지_않고_결과로_남긴다(monkeypatch) -> None:  # noqa: ANN001
    """2026-10-09 GitHub Models 새 주소가 200 에 빈 본문을 줘 시험 전체가 죽었다(25.1074)."""

    class 응답:
        status_code = 200
        text = ""

        def json(self) -> dict:
            raise ValueError("빈 본문")

    monkeypatch.setattr(probe.requests, "post", lambda url, **kw: 응답())
    complete, _ = probe.remote_backend("github", None)
    raw, usage = complete("p")
    assert raw.startswith("HTTP 200 JSON 아님") and usage == {}
