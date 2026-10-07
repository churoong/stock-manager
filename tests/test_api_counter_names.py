"""호출 카운터를 **한 소스에 한 이름으로** 세는지 (docs/infra.md 25.68).

CLAUDE.md 비용 규칙: *"무료 한도가 있는 API는 호출 카운터를 두고 80% 도달 시 경고,
100%에서 중단"*. 그 판정은 `api_usage` 의 한 줄을 본다. 그런데 같은 소스를 **두 이름으로**
세면 줄이 둘로 갈라지고, 한쪽에 쓴 만큼은 한도 계산에 **안 보인다.**

2026-09-21 에 실제로 그랬다. 재무·배당·공시는 `dart_opendart` 로 세는데 업종만 `dart` 였다.
업종은 한 번에 879회를 부른다 — 하루 한도 20,000 의 4.4% 가 카운터에 잡히지 않았다.
게다가 그 한 줄은 `limit_value` 도 없어 화면에는 **한도를 모르는 API** 가 하나 더 보였다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from batch.core import db
from batch.sources import yahoo_fund

뿌리 = Path(__file__).resolve().parent.parent


#: 카운터에 올리는 함수. `record_and_guard` 는 `record_api_call` 을 감싸 **상태까지** 돌려준다
#: (2026-09-22, docs/infra.md 25.117). 둘 다 같은 눈으로 본다
세는함수 = ("record_api_call", "record_and_guard")


def 호출들() -> list[tuple[str, int, str]]:
    """(파일, 줄, api_name). 상수가 아니면 코드 그대로 돌려준다.

    `batch/core/db.py` 는 뺀다. 거기 있는 것은 **부르는 자리가 아니라 배관**이다 —
    `record_and_guard` 가 받은 `api_name` 을 그대로 넘긴다. 배관을 부르는 자리로 세면
    "`api_name` 이라는 이름으로 센다" 는 거짓 양성이 나온다.
    """
    나온것 = []
    for 길 in sorted((뿌리 / "batch").rglob("*.py")):
        if "__pycache__" in str(길) or 길.name == "db.py":
            continue
        for 마디 in ast.walk(ast.parse(길.read_text(encoding="utf-8"))):
            if not isinstance(마디, ast.Call):
                continue
            이름 = 마디.func.attr if isinstance(마디.func, ast.Attribute) else getattr(마디.func, "id", "")
            if 이름 not in 세는함수 or len(마디.args) < 2:
                continue
            인자 = 마디.args[1]
            값 = 인자.value if isinstance(인자, ast.Constant) else ast.unparse(인자)
            나온것.append((str(길.relative_to(뿌리)), 마디.lineno, str(값)))
    return 나온것


def 풀기(값: str) -> str:
    """`yahoo_fund.SOURCE` 처럼 상수로 넘긴 것을 실제 값으로."""
    return yahoo_fund.SOURCE if 값 == "yahoo_fund.SOURCE" else 값


def test_부르는_곳을_찾아냈다() -> None:
    """0개면 아래가 공짜로 통과한다."""
    assert len(호출들()) >= 20


@pytest.mark.parametrize("호출", 호출들(), ids=lambda c: f"{c[0]}:{c[1]}")
def test_정해진_이름으로만_센다(호출: tuple[str, int, str]) -> None:
    파일, 줄, 값 = 호출

    assert 풀기(값) in db.API_NAMES, (
        f"{파일}:{줄} 가 {값!r} 로 센다. `db.API_NAMES` 에 없는 이름이다.\n"
        "같은 소스를 두 이름으로 세면 한도 카운터가 갈라져 한쪽이 안 보인다"
    )


def test_DART_를_한_이름으로_센다() -> None:
    """이 결함이 실제로 있었던 자리. 이름이 하나여야 한다."""
    dart이름 = {풀기(값) for _, _, 값 in 호출들() if "dart" in 풀기(값).lower()}

    assert dart이름 == {"dart_opendart"}, f"DART 를 여러 이름으로 센다: {sorted(dart이름)}"


def test_SEC_도_한_이름으로_센다() -> None:
    sec이름 = {풀기(값) for _, _, 값 in 호출들() if "sec" in 풀기(값).lower()}

    assert sec이름 == {"sec_edgar"}


class Test한도를_아는_곳은_한도를_넘긴다:
    """`limit_value` 가 없으면 화면이 그 API 를 '한도 모름' 으로 보여 준다."""

    def test_DART_호출은_모두_한도를_넘긴다(self) -> None:
        빠진곳 = []
        for 길 in sorted((뿌리 / "batch").rglob("*.py")):
            if "__pycache__" in str(길) or 길.name == "db.py":
                continue
            for 마디 in ast.walk(ast.parse(길.read_text(encoding="utf-8"))):
                if not isinstance(마디, ast.Call):
                    continue
                이름 = 마디.func.attr if isinstance(마디.func, ast.Attribute) else getattr(마디.func, "id", "")
                if 이름 not in 세는함수 or len(마디.args) < 2:
                    continue
                인자 = 마디.args[1]
                if not (isinstance(인자, ast.Constant) and 인자.value == "dart_opendart"):
                    continue
                if not any(kw.arg == "limit_value" for kw in 마디.keywords):
                    빠진곳.append(f"{길.relative_to(뿌리)}:{마디.lineno}")

        assert not 빠진곳, f"DART 를 세면서 한도를 안 넘긴다: {빠진곳}"


def test_업종이_실제로_부른_횟수를_센다() -> None:
    """계획한 수(`len(corps)`)를 세면, 다섯 번 만에 멈춰도 879 를 적는다.

    그러면 그날 남은 DART 작업(재무·배당·공시)이 **헛되이** 한도에 막힌다.
    """
    글 = (뿌리 / "batch" / "jobs" / "sectors.py").read_text(encoding="utf-8")

    assert '"dart_opendart"' in 글, "업종이 dart_opendart 로 세지 않는다"
    assert "count=len(corps)" not in 글, "계획한 수를 다시 세고 있다"
    assert "count=안_센것" in 글, "실제로 부른 횟수를 세지 않는다"


# ----------------------------------------------------------------------
# 세는 것은 절반이다 — **그 수를 보고 멈추는 것**이 나머지 절반 (25.117)
# ----------------------------------------------------------------------


class Test세고_나서_그_수를_본다:
    """CLAUDE.md 비용 규칙은 두 마디다 — **"80% 도달 시 경고, 100%에서 중단."**

    2026-09-22 까지 나머지 절반은 `financials` 와 `backfill_kr` **둘에만** 있었다.
    나머지 작업은 바깥 API 가 `020` 으로 거절할 때까지 계속 불렀다 — 그것은
    **넘고 나서 아는 것**이다. `insider_kr` 은 아예 세지도 않았다(주 1회 × 879회).
    """

    #: DART 를 부르는 작업. 한도(20,000/일)가 문서로 확인된 유일한 소스다
    #: (docs/data-sources.md — SEC·야후는 한도가 `[확인필요]` 라 "100% 중단" 을 정의할 수 없다)
    DART작업 = ("financials.py", "dividends.py", "sectors.py", "disclosures_kr.py", "insider_kr.py")

    @staticmethod
    def _글(이름: str) -> str:
        return (뿌리 / "batch" / "jobs" / 이름).read_text(encoding="utf-8")

    @pytest.mark.parametrize("이름", DART작업)
    def test_DART_를_부르면_센다(self, 이름: str) -> None:
        글 = self._글(이름)
        assert "dart_opendart" in 글, f"{이름} 이 DART 를 부르면서 카운터에 안 센다"

    @pytest.mark.parametrize("이름", DART작업)
    def test_센_수를_보고_멈춘다(self, 이름: str) -> None:
        """`result.limit_state` 만 보는 것으로는 모자라다 — 그건 바깥이 이미 거절한 뒤다."""
        글 = self._글(이름)
        본다 = "record_and_guard" in 글 or 'usage["state"]' in 글
        assert 본다, (
            f"{이름} 이 카운터를 세고도 그 수를 안 본다.\n"
            "  `db.record_and_guard(...) == 'blocked'` 로 **넘기 전에** 멈춘다"
        )

    def test_읽어_냈다(self) -> None:
        """파일 이름이 바뀌면 위 검사가 통째로 공짜가 된다."""
        for 이름 in self.DART작업:
            assert (뿌리 / "batch" / "jobs" / 이름).exists(), 이름


# ----------------------------------------------------------------------
# **그물이 한 방향만 봤다** (2026-09-23, docs/infra.md 25.178)
# ----------------------------------------------------------------------

#: DART 를 실제로 부르는 함수. 전부 `fetch_` 로 시작한다 (`batch/sources/dart*.py`).
#: 이름 규칙이 깨지면 `test_부르는_함수_이름_규칙이_유지된다` 가 먼저 깨진다
DART소스 = ("dart", "dart_dividends", "dart_disclosures", "dart_insider")

#: DART 를 부르는데 위 `DART작업` 에 없어도 되는 파일과 **왜인지**
부르지만_면제 = {
    # 지금은 비어 있다. 생기면 사유를 적는다 — 사유 없는 면제는 곧 잊힌 구멍이다
}


def DART를_부르는_파일() -> set[str]:
    """`batch/`·`scripts/` 에서 DART 원본 함수를 **실제로 부르는** 파일 (경로 문자열).

    import 만으로는 못 센다. `jobs/signals.py` 처럼 상수(`ANNUAL_REPORT_CODE`) 하나만
    가져다 쓰는 파일이 열 개가 넘는다 — 그것까지 세면 그물이 거짓 양성으로 가득 찬다.
    """
    나온것: set[str] = set()
    대상 = sorted(list((뿌리 / "batch").rglob("*.py")) + list((뿌리 / "scripts").rglob("*.py")))
    for 길 in 대상:
        if "__pycache__" in str(길) or "sources" in 길.parts:
            continue
        나무 = ast.parse(길.read_text(encoding="utf-8"))
        별칭: dict[str, str] = {}
        for 마디 in ast.walk(나무):
            if not isinstance(마디, ast.ImportFrom) or not 마디.module:
                continue
            if 마디.module == "batch.sources":
                별칭 |= {a.asname or a.name: a.name for a in 마디.names if a.name in DART소스}
            elif 마디.module.startswith("batch.sources."):
                잎 = 마디.module.rsplit(".", 1)[-1]
                if 잎 in DART소스:
                    별칭 |= {a.asname or a.name: 잎 for a in 마디.names}
        for 마디 in ast.walk(나무):
            if not isinstance(마디, ast.Call):
                continue
            함수 = 마디.func
            if (
                isinstance(함수, ast.Attribute)
                and isinstance(함수.value, ast.Name)
                and 함수.value.id in 별칭
                and 함수.attr.startswith("fetch_")
            ) or (isinstance(함수, ast.Name) and 함수.id in 별칭 and 함수.id.startswith("fetch_")):
                나온것.add(str(길.relative_to(뿌리)))
                break
    return 나온것


@pytest.mark.parametrize("이름", DART소스)
def test_부르는_함수_이름_규칙이_유지된다(이름: str) -> None:
    """위 훑기는 `fetch_` 로 시작하는 이름만 본다. 규칙이 깨지면 그물이 조용히 눈을 감는다."""
    나무 = ast.parse((뿌리 / "batch" / "sources" / f"{이름}.py").read_text(encoding="utf-8"))
    네트워크 = {
        마디.name
        for 마디 in 나무.body
        if isinstance(마디, ast.FunctionDef)
        and any(
            isinstance(안, ast.Attribute)
            and isinstance(안.value, ast.Name)
            and 안.value.id == "requests"
            and 안.attr in ("get", "post")
            for 안 in ast.walk(마디)
        )
    }
    assert 네트워크, f"batch/sources/{이름}.py 에서 네트워크 함수를 하나도 못 찾았다"

    어긋난것 = sorted(n for n in 네트워크 if not n.startswith(("fetch_", "_")))
    assert not 어긋난것, (
        f"batch/sources/{이름}.py 에 `fetch_` 가 아닌 네트워크 함수가 있다: {어긋난것}.\n"
        "이름을 맞추거나 `DART를_부르는_파일()` 의 훑기를 고쳐라 — 지금 그물이 이것을 못 본다"
    )


def test_DART_훑기가_실제로_찾아_냈다() -> None:
    """조용히 비면 아래가 공짜로 통과한다."""
    찾은것 = DART를_부르는_파일()
    assert len(찾은것) >= 5, f"DART 를 부르는 파일을 {len(찾은것)}개밖에 못 찾았다: {sorted(찾은것)}"
    assert "batch/jobs/financials.py" in 찾은것


def test_DART_를_부르는_곳이_모두_등록돼_있다() -> None:
    """**반대 방향** (docs/infra.md 25.178).

    `Test세고_나서_그_수를_본다.DART작업` 은 손으로 적은 다섯이고, 그 목록에 있는 것이
    세고 멈추는지만 본다. **새로 DART 를 부르는 작업이 생겨도 목록에 안 넣으면 그만**이다 —
    그 작업은 카운터도 안 세고 멈추지도 않는 채로 하루 한도(20,000)를 태울 수 있고,
    그러면 그날 남은 DART 작업이 전부 헛되이 막힌다 (CLAUDE.md 비용 규칙).
    """
    등록된것 = {f"batch/jobs/{이름}" for 이름 in Test세고_나서_그_수를_본다.DART작업}
    빠진것 = sorted(DART를_부르는_파일() - 등록된것 - set(부르지만_면제))

    assert not 빠진것, (
        f"DART 를 부르는데 `DART작업` 에 없는 파일: {빠진것}\n"
        "그 목록에 넣어 세고 멈추게 하거나, `부르지만_면제` 에 **사유와 함께** 적어라"
    )


def test_등록만_되고_안_부르는_것이_없다() -> None:
    """목록이 낡는 쪽. 작업을 지웠는데 이름이 남으면 검사가 헛돈다."""
    등록된것 = {f"batch/jobs/{이름}" for 이름 in Test세고_나서_그_수를_본다.DART작업}
    안부르는것 = sorted(등록된것 - DART를_부르는_파일())

    assert not 안부르는것, f"`DART작업` 에 있는데 DART 를 안 부른다: {안부르는것}"


def test_면제에는_사유가_있다() -> None:
    짧은것 = [이름 for 이름, 사유 in 부르지만_면제.items() if len(str(사유).strip()) < 20]

    assert not 짧은것, f"면제 사유가 너무 짧다: {짧은것}"


def test_세면서_멈추지_않는_파일이_없다() -> None:
    """`dart_opendart` 로 세는 파일은 모두 `DART작업` 에 있어야 한다.

    세기만 하고 멈추지 않으면 "80% 경고, 100% 중단" 의 절반만 지키는 것이다 (25.117).
    """
    세는파일 = {
        파일.replace("\\", "/") for 파일, _, 값 in 호출들() if 풀기(값) == "dart_opendart"
    }
    등록된것 = {f"batch/jobs/{이름}" for 이름 in Test세고_나서_그_수를_본다.DART작업}
    빠진것 = sorted(세는파일 - 등록된것)

    assert not 빠진것, f"DART 를 세는데 멈추는지 확인하지 않는 파일: {빠진것}"


#: 야후(yfinance)를 실제로 부르는 함수들 (`batch/sources/yfinance_src`)
야후함수 = ("fetch_daily_bars", "fetch_daily_quotes", "fetch_earnings_dates")


def 야후를_부르는_파일() -> dict[str, bool]:
    """{파일: "yfinance" 로 세는가}. `batch/sources` 는 배관이라 뺀다 (docs/infra.md 25.200)."""
    나온것: dict[str, bool] = {}
    for 길 in sorted((뿌리 / "batch").rglob("*.py")):
        if "__pycache__" in str(길) or "sources" in 길.parts:
            continue
        나무 = ast.parse(길.read_text(encoding="utf-8"))
        부른다 = 센다 = False
        for 마디 in ast.walk(나무):
            if not isinstance(마디, ast.Call):
                continue
            이름 = 마디.func.attr if isinstance(마디.func, ast.Attribute) else getattr(마디.func, "id", "")
            if 이름 in 야후함수:
                부른다 = True
            if 이름 in 세는함수 and len(마디.args) >= 2 and isinstance(마디.args[1], ast.Constant):
                센다 = 센다 or 마디.args[1].value == "yfinance"
        if 부른다:
            나온것[str(길.relative_to(뿌리))] = 센다
    return 나온것


#: 야후를 부르지만 세지 않아도 되는 파일과 사유
야후_면제 = {
    "batch/jobs/step0_check.py": "환경 점검 도구다. DB 연결 자체를 점검 항목으로 따로 보므로 카운터를 쓸"
    " 자리가 없고, 사람이 처음 한 번 손으로 돌린다 (docs/infra.md 25.200)",
}


def test_야후_훑기가_실제로_찾아_냈다() -> None:
    assert len(야후를_부르는_파일()) >= 6, 야후를_부르는_파일()


def test_야후를_부르는_곳은_모두_센다() -> None:
    """**25.200.** 실적 예정일(종목마다 한 번)과 ETF 일봉이 야후를 부르면서 한 번도 안 셌다.

    야후는 공식 한도가 없어 카운터가 막지는 않는다(docs/data-sources.md 4절). 대신 **얼마나 부르는지
    보는 유일한 창**이다. 가장 많이 부르는 작업이 빠지면 429 로 막혔을 때 원인을 못 찾는다.
    """
    안세는것 = sorted(
        파일 for 파일, 센다 in 야후를_부르는_파일().items() if not 센다 and 파일 not in 야후_면제
    )

    assert not 안세는것, f"야후를 부르면서 `yfinance` 로 세지 않는 파일: {안세는것}"


def test_야후_면제가_낡지_않았다() -> None:
    부르는것 = 야후를_부르는_파일()
    assert all(파일 in 부르는것 for 파일 in 야후_면제), "더는 야후를 안 부르는 파일의 면제가 남아 있다"
    assert all(len(사유) > 30 for 사유 in 야후_면제.values())
