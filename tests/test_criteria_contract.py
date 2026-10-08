"""근거표의 파이썬↔웹 약속 (CLAUDE.md 절대 규칙).

**왜 있나.** 절대 규칙 하나가 이것이다 — "모든 추천은 왜 추천했는지가 분명해야 하고,
사용자가 확인할 수 있어야 한다. 근거표를 만들 수 없는 추천은 표시하지 않는다"
(2026-09-16 사용자 확정).

근거표는 **두 언어에 걸쳐 있다.** 파이썬이 `rationale_data.criteria` 에 행을 담고,
웹의 `parseCriteria()` 가 그것을 걸러 그린다. 그런데 웹의 거르개는 모양이 안 맞는 행을
**조용히 버린다** — 던지지도, 로그를 남기지도 않는다.

그래서 이런 일이 가능하다. 파이썬에서 `"display"` 를 `"value"` 로 바꾼다. 파이썬 테스트는
전부 통과한다. 웹 테스트도 전부 통과한다(웹은 자기가 만든 가짜 행으로 시험한다). 화면은
멀쩡히 뜬다. 다만 **모든 추천에서 근거표가 사라진다.** 아무도 모른다. 절대 규칙이
그날부터 깨진 채로 도는데 빨간불이 하나도 안 켜진다.

여기서 지키는 것:

1. **웹이 요구하는 열쇠를 웹 소스에서 읽어 온다.** 이 파일에 손으로 옮겨 적으면, 웹이
   바뀌었을 때 이 테스트가 옛날 약속을 지키며 통과한다 — 거르개 노릇을 못 한다
2. **근거표를 만드는 곳을 모두 본다.** 종목 신호·ETF·위성 ETF·적립·매도 플래그·내부자 매매.
   하나만 어긋나도 그 화면의 확인이 사라진다
3. **행이 실제로 거르개를 통과하는지** 본다. 열쇠가 있는 것과 웹이 받아 주는 것은 다르다
4. **근거표가 빈 채로 나올 수 있는가.** 그 구멍이 **닿지 않는 이유**가
   `sizing_criteria()` 의 "비중 축소 없음" 행 하나다. 그 행을 지우면 규칙이 깨진다
5. **추천을 내는 넷이 모두 거르개를 지나는가** (2026-09-23, docs/infra.md 25.174).
   `verifiable()` 은 주석에 "화면이 넷이고 하나라도 빠뜨리면 규칙이 샌다" 고 적어 두고도
   종목 신호 한 곳에서만 불렸다. ETF·위성·적립은 규칙 밖에서 돌고 있었다

   (2026-09-23 정정: 여기 "지금 화면은 근거표가 비면 표를 안 그리고 추천은 그대로 보여
   준다" 고 적어 두었던 줄을 지웠다. 그게 바로 25.174 에서 고친 고장이다 —
   **알고도 안 고친 것을 설명으로 적어 두면 그 설명이 고장을 정당화한다.**)
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from batch.services import accumulation, etf, etf_satellite, insider, sell_flags, signals, verdict
from batch.services import criteria as criteria_svc

웹_소스 = Path(__file__).resolve().parent.parent / "web" / "lib" / "recommend.ts"


def 웹이_요구하는_문자열_열쇠() -> set[str]:
    """`parseCriteria()` 의 거르개가 문자열이기를 요구하는 열쇠들."""
    소스 = 웹_소스.read_text(encoding="utf-8")
    본문 = 소스[소스.index("export function parseCriteria") :]
    본문 = 본문[: 본문.index("\n}")]
    return set(re.findall(r'typeof c\.(\w+) === "string"', 본문))


def 웹이_읽는_모든_열쇠() -> set[str]:
    """`Criterion` 인터페이스가 선언한 필드. 화면이 실제로 꺼내 쓰는 것들이다."""
    소스 = 웹_소스.read_text(encoding="utf-8")
    본문 = 소스[소스.index("export interface Criterion {") :]
    본문 = 본문[: 본문.index("\n}")]
    # 주석을 걷어내고 `이름: 타입;` 만 줍는다
    없앤것 = re.sub(r"/\*.*?\*/", "", 본문, flags=re.S)
    return set(re.findall(r"^\s{2}(\w+)[?]?:", 없앤것, flags=re.M))


def 웹의_거르개(행: dict, 요구: set[str]) -> bool:
    """`parseCriteria()` 가 이 행을 남기는가. 웹 코드와 같은 판정이다."""
    return bool(행) and all(isinstance(행.get(key), str) for key in 요구)


#: 근거표 행을 만드는 곳 전부. 새로 생기면 여기에 더한다
만드는_곳 = {
    "종목 신호": lambda: signals._criterion("라벨", "보이는 값", "문턱", "prices", "2026-09-18"),
    "ETF": lambda: etf._criterion("라벨", "보이는 값", "문턱", "yahoo", "2026-09-18", True),
    "위성 ETF 참고행": lambda: etf_satellite._info("라벨", "보이는 값", "krx", "2026-09-18"),
    "적립": lambda: accumulation._criterion("라벨", "보이는 값", "문턱", "prices", None, None),
    "매도 플래그": lambda: sell_flags._criterion("라벨", "보이는 값", "문턱", "prices", None),
    # 종목 분석 의견의 근거표 (docs/analysis.md, 25.1016)
    "종목 분석": lambda: verdict._row("라벨", "보이는 값", "문턱", "scores", "2026-10-08"),
    # 2026-09-21 에 더했다. **여섯째였는데 이 목록에 없었다** — 위 머리말이 "다섯 곳"
    # 이라고 적혀 있던 그 사이에 여섯째가 생긴 것이다(docs/infra.md 25.94).
    # 이 행은 다른 다섯과 달리 `_criterion` 도우미를 안 쓰고 **직접 딕셔너리를 짓는다** —
    # 그래서 열쇠 하나가 빠져도 아무도 모른다. 그런 자리일수록 대 봐야 한다
    "내부자 매매 참고행": lambda: insider.criteria_rows(
        insider.InsiderSummary(
            as_of="2026-09-18", window_days=90, since="2026-06-20",
            buy_shares=1000, sell_shares=200, buyers=["갑"], sellers=["을"],
            latest_filed="2026-09-17", reports=2,
        ),
        listed_shares=1_000_000,
    )[0],
}


#: 위 목록이 가리키는 **실제 함수**. 훑기가 찾아낸 것과 이것을 맞대 본다
만드는_함수 = {
    "batch/services/signals.py::_criterion",
    "batch/services/etf.py::_criterion",
    "batch/services/etf_satellite.py::_info",
    "batch/services/accumulation.py::_criterion",
    "batch/services/sell_flags.py::_criterion",
    "batch/services/insider.py::criteria_rows",
    "batch/services/verdict.py::_row",
}


def 근거행을_짓는_함수들() -> set[str]:
    """`batch/` 를 훑어 **네 열쇠를 다 가진 딕셔너리**를 짓는 함수를 찾는다.

    위 목록은 "새로 생기면 여기에 더한다" 고 적혀 있을 뿐, 안 더해도 아무도 모른다.
    실제로 2026-09-21 에 여섯째(`insider.criteria_rows`)가 **빠져 있었다.**
    사람의 기억 대신 훑기로 센다.
    """
    나온것: set[str] = set()
    for path in sorted((Path(__file__).resolve().parent.parent / "batch").rglob("*.py")):
        나무 = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(나무):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Dict):
                    continue
                열쇠 = {
                    k.value for k in sub.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
                }
                if {"label", "display", "threshold", "source"} <= 열쇠:
                    나온것.add(f"{path.relative_to(Path(__file__).resolve().parent.parent).as_posix()}::{node.name}")
                    break
    return 나온것


def test_훑기가_실제로_찾아_냈다() -> None:
    """조용히 빈 집합을 내면 아래가 공짜로 통과한다."""
    assert len(근거행을_짓는_함수들()) >= 5


def test_근거행을_짓는_곳이_모두_등록돼_있다() -> None:
    """**그물은 방향이 있다** (docs/infra.md 25.0).

    위의 `만드는_곳` 은 등록된 것만 대 본다. 등록을 잊은 새 산지는
    이 테스트가 아니면 영영 안 걸린다 — 그 화면의 근거표가 조용히 사라져도 모른다.
    """
    찾은것 = 근거행을_짓는_함수들()
    빠진것 = 찾은것 - 만드는_함수

    assert not 빠진것, (
        f"근거표 행을 짓는데 이 파일의 `만드는_곳` 에 없는 곳이 있다: {sorted(빠진것)}\n"
        "웹의 거르개는 모양이 안 맞는 행을 **조용히 버린다** — 대 보지 않으면 모른다"
    )
    assert not (만드는_함수 - 찾은것), (
        f"등록해 둔 함수가 사라졌거나 이름이 바뀌었다: {sorted(만드는_함수 - 찾은것)}"
    )


def test_등록한_수와_대_보는_수가_같다() -> None:
    """함수는 등록했는데 `만드는_곳` 에 공장을 안 넣으면 대 보지 않는다."""
    assert len(만드는_곳) == len(만드는_함수), (
        f"함수 {len(만드는_함수)}개를 알면서 {len(만드는_곳)}개만 대 보고 있다"
    )


def test_웹_소스에서_약속을_읽어_냈다() -> None:
    """읽기가 조용히 빈 집합을 내면 아래 테스트가 전부 통과한다. 가장 위험한 실패다."""
    요구 = 웹이_요구하는_문자열_열쇠()

    assert 요구 == {"label", "display", "threshold", "source"}, (
        f"parseCriteria 의 거르개가 바뀌었다: {요구}. 바뀐 것이 맞다면 이 줄을 고친다"
    )
    assert 웹이_읽는_모든_열쇠() == {"label", "display", "threshold", "source", "as_of", "passed"}


@pytest.mark.parametrize("이름", sorted(만드는_곳))
def test_만든_행이_웹의_거르개를_통과한다(이름: str) -> None:
    행 = 만드는_곳[이름]()

    assert 웹의_거르개(행, 웹이_요구하는_문자열_열쇠()), (
        f"{이름} 의 행이 웹에서 조용히 버려진다. 화면에는 오류 없이 근거표만 사라진다"
    )


@pytest.mark.parametrize("이름", sorted(만드는_곳))
def test_화면이_읽는_열쇠가_모두_있다(이름: str) -> None:
    행 = 만드는_곳[이름]()

    빠진것 = 웹이_읽는_모든_열쇠() - set(행)

    # 거르개는 넷만 보지만 화면은 as_of(기준일 낡음 표시)·passed(통과/탈락/참고)도 쓴다.
    # 빠지면 undefined 가 흘러가 "참고" 로 그려지거나 날짜가 비어 보인다
    assert not 빠진것, f"{이름} 에 {빠진것} 가 없다"


@pytest.mark.parametrize("이름", sorted(만드는_곳))
def test_행이_JSON_으로_오갈_수_있다(이름: str) -> None:
    행 = 만드는_곳[이름]()

    # DB 에는 문자열로 들어간다. date 객체 같은 것이 섞이면 저장 자체가 터진다
    되돌린것 = json.loads(json.dumps(행))

    assert 되돌린것 == 행
    assert 되돌린것["passed"] in (True, False, None), "passed 는 통과·탈락·참고 셋뿐이다"
    assert 되돌린것["as_of"] is None or isinstance(되돌린것["as_of"], str)


class Test거르개가_웹과_같은_판정을_한다:
    """`signals.displayable()` 이 웹의 `parseCriteria()` 와 어긋나면 막는 구실을 못 한다."""

    def test_요구하는_열쇠가_웹과_같다(self) -> None:
        assert set(signals.CRITERION_REQUIRED_KEYS) == 웹이_요구하는_문자열_열쇠()

    @pytest.mark.parametrize("이름", sorted(만드는_곳))
    def test_같은_행에_같은_답을_낸다(self, 이름: str) -> None:
        행 = 만드는_곳[이름]()
        요구 = 웹이_요구하는_문자열_열쇠()

        assert signals.displayable(행) == 웹의_거르개(행, 요구)

    @pytest.mark.parametrize(
        "망가뜨릴_열쇠", ["label", "display", "threshold", "source"]
    )
    def test_열쇠가_빠진_행은_둘_다_버린다(self, 망가뜨릴_열쇠: str) -> None:
        행 = 만드는_곳["종목 신호"]()
        del 행[망가뜨릴_열쇠]

        assert signals.displayable(행) is False
        assert 웹의_거르개(행, 웹이_요구하는_문자열_열쇠()) is False

    def test_문자열이_아니면_버린다(self) -> None:
        # 숫자를 그대로 넣으면 웹이 조용히 버린다. 파이썬에서 먼저 잡는다
        행 = {**만드는_곳["종목 신호"](), "display": 12345}

        assert signals.displayable(행) is False


class Test확인할_수_없는_추천은_내보내지_않는다:
    """CLAUDE.md 절대 규칙 (2026-09-16 사용자 확정)."""

    def _신호(self, criteria: list[dict] | None) -> signals.Signal:
        return signals.Signal(
            stock_id=1, ticker="005930", name="삼성전자", horizon="long",
            signal_type="value", buy_zone_low=1.0, buy_zone_high=2.0, tranches=[],
            target_price=3.0, stop_price=0.5, currency="KRW", rationale_text="문장",
            rationale_data={} if criteria is None else {"criteria": criteria},
        )  # fmt: skip

    def test_참고_행만_있으면_내보내지_않는다(self) -> None:
        """목표·손절·권장 금액·비중 같은 참고 행은 모든 신호에 붙는다 — 그것만으로는 왜 추천했는지 모른다
        (docs/infra.md 25.620, 감사: 판정 행 0줄이어도 늘 통과했다)."""
        참고 = [{"label": "목표·손절", "display": "x", "threshold": "y", "source": "z", "as_of": None, "passed": None}] * 3
        assert signals.verifiable(self._신호(참고)) is False
        통과 = {"label": "정배열", "display": "x", "threshold": "y", "source": "z", "as_of": "2026-09-25", "passed": True}
        assert signals.verifiable(self._신호([*참고, 통과])) is True

    def test_비중_행은_판정이_아니라_참고다(self) -> None:
        rows = signals.sizing_criteria(_빈_신호입력(), {"volatility_factor": 0.8, "volatility_ann": 0.4})
        assert all(r["passed"] is None for r in rows)

    def test_비중_행은_어느_창의_값인지_적는다(self) -> None:
        """성과 지표는 1Y·3Y·5Y 세 행이다 — 근거가 창을 말하지 않아 어느 칸의 숫자인지 가를 수 없었다 (docs/infra.md 25.939, 감사)."""
        from dataclasses import replace

        inp = replace(_빈_신호입력(), metrics_window="1Y")
        rows = signals.sizing_criteria(inp, {"volatility_factor": 0.8, "volatility_ann": 0.41, "mdd_factor": 0.9, "mdd": -0.5})
        assert rows[0]["display"].startswith("연환산 변동성(1Y) 41.0%")
        assert rows[1]["display"].startswith("MDD(1Y) ")

    def test_근거표가_있으면_내보낸다(self) -> None:
        assert signals.verifiable(self._신호([만드는_곳["종목 신호"]()])) is True

    def test_근거표가_없으면_막는다(self) -> None:
        assert signals.verifiable(self._신호(None)) is False

    def test_근거표가_비어_있으면_막는다(self) -> None:
        assert signals.verifiable(self._신호([])) is False

    def test_행은_있는데_전부_버려지면_막는다(self) -> None:
        # 가장 위험한 모양이다. 파이썬에서는 "근거 세 줄" 인데 화면에서는 빈 표가 된다
        망가진행 = {"이름": "라벨", "값": "보이는 값"}

        assert signals.verifiable(self._신호([망가진행, 망가진행])) is False

    def test_한_줄이라도_살면_내보낸다(self) -> None:
        성한행 = 만드는_곳["종목 신호"]()

        assert signals.verifiable(self._신호([{"엉뚱": 1}, 성한행])) is True


class Test근거표가_비는_일은_없다:
    """위 장치가 있어도 **실제로 막히는 일이 없어야** 정상이다.

    `sizing_criteria()` 의 "비중 축소 없음" 행 하나가 그것을 보장한다. 지우면
    지표를 모르는 종목의 추천이 통째로 사라진다.
    """

    def test_지표를_하나도_몰라도_비중_행이_남는다(self) -> None:
        rows = signals.sizing_criteria(_빈_신호입력(), {})

        assert rows, "size_data 가 비었는데 근거표가 빈 목록이 됐다"
        assert rows[0]["label"] == "비중 축소 없음"

    def test_모르는_것을_위험하다고_말하지_않는다(self) -> None:
        # "몰라서 안 줄였다" 와 "재 보니 괜찮아서 안 줄였다" 는 다른 말이다
        rows = signals.sizing_criteria(_빈_신호입력(), {})

        assert "몰라" in rows[0]["display"]

    def test_거르개를_통과하는_행이_남는다(self) -> None:
        # 행이 있어도 모양이 안 맞으면 웹에서 버려져 결국 빈 표가 된다
        요구 = 웹이_요구하는_문자열_열쇠()

        rows = signals.sizing_criteria(_빈_신호입력(), {})

        assert [row for row in rows if 웹의_거르개(row, 요구)]


def _빈_신호입력() -> signals.SignalInput:
    """필수 인자만 채운 SignalInput. 지표는 전부 모르는 상태."""
    return signals.SignalInput(stock_id=1, ticker="005930", name="삼성전자", market="KOSPI")


# ----------------------------------------------------------------------
# 추천을 내는 네 화면이 **모두** 같은 판정을 지나는가 (docs/infra.md 25.174)
# ----------------------------------------------------------------------

뿌리 = Path(__file__).resolve().parent.parent

#: 추천을 내는 네 갈래 → (그 판정을 하는 파일, 그 파일에 있어야 할 글, 왜 거기인가)
#:
#: `verifiable()` 의 주석이 **"화면이 넷이고 하나라도 빠뜨리면 규칙이 샌다"** 고 적어 두고도
#: 정작 그 함수를 부르는 곳은 종목 신호 하나였다 (2026-09-23 확인, docs/infra.md 25.174).
거르는_곳 = {
    "종목 신호": (
        "batch/jobs/signals.py",
        "sg.verifiable(signal)",
        "신호는 통과한 것만 저장한다. 내보내지 않는 것으로 막는다",
    ),
    "ETF 핵심": (
        "batch/services/etf.py",
        "criteria.drop_unverifiable",
        "미국·국내 둘 다 `score_within_categories()` 를 지난다",
    ),
    "ETF 위성": (
        "batch/services/etf_satellite.py",
        "core.score_within_categories",
        "핵심의 점수 함수를 그대로 쓴다. 거기서 함께 걸린다",
    ),
    "적립 종목": (
        "batch/services/accumulation.py",
        "criteria.drop_unverifiable",
        "순위를 매기는 `rank()` 가 그 자리다",
    ),
}


@pytest.mark.parametrize("화면", sorted(거르는_곳))
def test_네_화면이_모두_거르개를_지난다(화면: str) -> None:
    """**함수가 옳은 것과 부르는 쪽이 옳은 것은 다른 일이다** (docs/infra.md 25.151·25.35)."""
    경로, 찾을것, 사유 = 거르는_곳[화면]
    글 = (뿌리 / 경로).read_text(encoding="utf-8")

    assert 찾을것 in 글, (
        f"{화면} 이 근거표 없는 추천을 거르지 않는다 ({경로} 에 `{찾을것}` 이 없다).\n"
        f"{사유}\n"
        "CLAUDE.md 절대 규칙: 근거표를 만들 수 없는 추천은 표시하지 않는다"
    )


class Test근거를_못_만들면_추천에서_뺀다:
    """`criteria.drop_unverifiable()` 이 실제로 하는 일."""

    def _ETF(self, rows: list[dict], symbol: str = "SPY") -> etf.Evaluation:
        return etf.Evaluation(
            symbol=symbol, name="이름", profile=None, passed=True, bucket="미국 주식",
            excluded_reason=None, criteria=list(rows), country="US", category="Large Blend",
            metrics={"expense": 0.0003, "assets": 5e11},
        )  # fmt: skip

    def test_성한_근거는_그대로_통과한다(self) -> None:
        ev = self._ETF([만드는_곳["ETF"]()])

        뺀것 = etf.score_within_categories([ev])

        assert 뺀것 == []
        assert ev.passed is True
        assert ev.rank_in_category == 1

    def test_근거가_없으면_뺀다(self) -> None:
        ev = self._ETF([])

        뺀것 = etf.score_within_categories([ev])

        assert 뺀것 == ["SPY"]
        assert ev.passed is False
        assert ev.excluded_reason == criteria_svc.NO_CRITERIA_REASON
        assert ev.rank_in_category is None, "뺀 것에 순위를 매기면 화면에 다시 나온다"

    def test_행은_있는데_전부_버려지면_뺀다(self) -> None:
        # 파이썬에서는 "근거 두 줄" 인데 화면에서는 빈 표가 되는 모양이다
        ev = self._ETF([{"이름": "라벨", "값": "보이는 값"}])

        assert etf.score_within_categories([ev]) == ["SPY"]

    def test_뺀_것은_집단_크기에서도_빠진다(self) -> None:
        """**순위를 매기기 전에 빼야 한다.** 나중에 빼면 남은 것의 순위가 헛것을 센다."""
        성한것 = self._ETF([만드는_곳["ETF"]()], symbol="VOO")
        빈것 = self._ETF([], symbol="SPY")

        etf.score_within_categories([성한것, 빈것])

        assert 성한것.category_size == 1, "빠진 후보를 세고 만든 순위는 사용자에게 거짓말이다"
        assert 성한것.rank_in_category == 1

    def test_적립도_같이_뺀다(self) -> None:
        j = accumulation.Judgement(
            stock_id=1, ticker="005930", name="삼성전자", market="KOSPI", passed=True,
            first_failed_gate=None, excluded_reason=None, criteria=[],
            metrics={"margin_std": 0.1, "min_roe": 0.1, "max_debt": 0.5, "retained_up": 4},
        )  # fmt: skip

        뺀것 = accumulation.rank([j])

        assert 뺀것 == ["005930"]
        assert j.passed is False
        assert j.excluded_reason == criteria_svc.NO_CRITERIA_REASON
        assert j.first_failed_gate == accumulation.NO_CRITERIA_GATE
        assert j.rank is None and j.group_size is None

    def test_적립의_깔때기_칸_이름이_웹에도_있다(self) -> None:
        """이름이 어긋나면 화면은 `근거` 라는 글자만 덩그러니 보여 준다."""
        글 = (뿌리 / "web" / "lib" / "accumulation.ts").read_text(encoding="utf-8")

        assert f"{accumulation.NO_CRITERIA_GATE}:" in 글, (
            f"web/lib/accumulation.ts 의 GATE_LABELS 에 `{accumulation.NO_CRITERIA_GATE}` 가 없다"
        )


class Test판정의_정의처는_한_곳이다:
    """넷이 같은 판정을 써야 한다. 옮겨 적으면 그때부터 넷이 따로 논다."""

    def test_신호도_같은_모듈을_쓴다(self) -> None:
        글 = (뿌리 / "batch" / "services" / "signals.py").read_text(encoding="utf-8")

        assert "from batch.services.criteria import" in 글
        assert signals.CRITERION_REQUIRED_KEYS is criteria_svc.CRITERION_REQUIRED_KEYS

    def test_같은_판정을_낸다(self) -> None:
        성한행 = 만드는_곳["종목 신호"]()

        assert criteria_svc.usable([성한행]) is True
        assert criteria_svc.usable([]) is False
        assert criteria_svc.usable(None) is False
