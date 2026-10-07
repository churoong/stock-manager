"""추천이 **왜 비어 있는지** 를 두 언어가 같은 기준으로 말하는가 (docs/infra.md 25.86).

문턱(모멘텀 126거래일 · 리스크 200거래일)이 이제 **두 곳에** 있다 —
파이썬 `scripts/catchup_report.py`(텔레그램 알림)와 웹 `web/lib/whyEmpty.ts`(화면).
묶어 두지 않으면 한쪽만 고쳐질 때 갈라지고, **알림과 화면이 다른 말을 한다.**
그것이 이 저장소에서 가장 자주 나온 고장 모양이다(25.0 "한 규칙이 두 곳에 있다").

**파이썬이 단일 정의처다.** 여기서 웹 소스를 읽어 대 본다 — 파이썬 테스트가 웹 소스를
읽는 것은 이 저장소의 기존 방식이다(`.github/workflows/tests.yml` 의 `paths` 주석).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(뿌리 / "scripts"))

import catchup_report as report  # noqa: E402

WHY_EMPTY = (뿌리 / "web" / "lib" / "whyEmpty.ts").read_text(encoding="utf-8")


def 웹상수(이름: str) -> int:
    m = re.search(rf"export const {이름} = (\d+);", WHY_EMPTY)
    assert m, f"{이름} 을 web/lib/whyEmpty.ts 에서 못 찾았다 — 모양이 바뀌었나"
    return int(m.group(1))


@pytest.mark.parametrize(
    "이름,파이썬값",
    [("MOMENTUM_DAYS", report.MOMENTUM_DAYS), ("RISK_DAYS", report.RISK_DAYS)],
)
def test_문턱이_두_언어에서_같다(이름: str, 파이썬값: int) -> None:
    assert 웹상수(이름) == 파이썬값, (
        f"{이름} 이 갈라졌다: 파이썬 {파이썬값} · 웹 {웹상수(이름)}.\n"
        "알림(텔레그램)과 화면이 다른 말을 하게 된다.\n"
        "단일 정의처는 scripts/catchup_report.py 다 — 웹을 맞춰라"
    )


def test_읽어_냈다() -> None:
    """정규식이 빗나가 0개면 위가 공짜로 통과한다."""
    assert len(WHY_EMPTY) > 500
    assert "export function whyNoSignals" in WHY_EMPTY


class Test해도_소용없는_일을_시키지_않는다:
    """**이 파일의 요지다.**

    2026-09-21 까지 국내 쪽 빈 화면은 `signals` 표가 0행이면 무조건 이렇게 말했다.

    > 아직 신호를 계산한 적이 없습니다. **매수 신호 배치를 먼저 돌리세요**

    그런데 따라잡기 7단계(`d1-catchup.yml`)가 신호 배치를 **매일 돌리고 있었다.**
    돌았지만 재료가 없어 0건이었을 뿐이다. 화면이 오진하고, 사용자에게 해도 소용없는
    일을 시켰다. 2026-09-19 D1 실측이 정확히 그 꼴이다 — 시세 88거래일, 재무 0, 업종 0.
    """

    def test_따라잡기가_정말_신호를_돌린다(self) -> None:
        """화면의 옛 안내가 왜 틀렸는지의 근거. 이 단계가 사라지면 이 항목을 다시 따져야 한다."""
        워크플로 = (뿌리 / ".github" / "workflows" / "d1-catchup.yml").read_text(encoding="utf-8")

        assert "batch.jobs.signals" in 워크플로, "따라잡기가 신호를 안 돌린다면 옛 안내가 맞았던 셈이다"

    def test_돌았는데_재료가_없는_경우를_가린다(self) -> None:
        assert "signalRuns" in WHY_EMPTY
        assert "낼 것이 없었습니다" in WHY_EMPTY

    def test_그_경우에는_돌리라고_하지_않는다(self) -> None:
        몸통 = WHY_EMPTY.split("const 모자람 = missingInputs(재료);", 1)[1]

        assert "돌리세요" not in 몸통, "재료가 없는데 배치를 돌리라고 시킨다"

    def test_못_읽은_것을_없는_것으로_치지_않는다(self) -> None:
        """`null` 을 0 으로 치면 있는데 없다고 말한다 (25.74)."""
        assert "!== null" in WHY_EMPTY

    def test_알림도_같은_재료를_본다(self) -> None:
        """텔레그램 쪽(25.85)과 화면이 **같은 셋**을 본다 — 시세 거래일·재무·업종."""
        for 낱말 in ("재무", "업종"):
            assert 낱말 in WHY_EMPTY, f"화면이 {낱말} 를 안 본다"
        assert report.모자란_재료(report.Facts(financial_companies=0, sectors=0)) == ["재무", "업종"]
