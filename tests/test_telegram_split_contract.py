"""텔레그램 나누기를 **두 언어가 같게 하는지** (docs/infra.md 25.58).

**왜 있나.** 2026-09-21 까지 웹 쪽 `sendTelegram` 은 길이를 전혀 보지 않았다. 파이썬만
`split_message` 로 나눴다. 장중 알림은 밀린 것을 최대 50건 **한 통으로** 묶어 보내는데
(`bundleMessage`), 긴 종목명이 섞이면 그 한 통이 텔레그램 상한 4,096자를 넘는다. 넘으면
`ok:false, "message is too long"` 으로 거절당하고, 경로는 **보낸 뒤에야** `sent_at` 을
찍으므로 같은 50건이 대기열에 그대로 남는다 — 5분마다 같은 실패를 되풀이하며 **스스로
낫지 않는다.** 막힌 것이 텔레그램이라 막혔다고 알릴 수도 없다.

고치면서 규칙이 **두 곳**이 되었다. 한쪽만 고치면 배치 리포트와 장중 알림이 서로 다르게
잘린다. 그래서 답안을 `tests/fixtures/telegram_split.json` 하나에 두고 양쪽이 읽는다.
이 파일은 파이썬 쪽을, `web/__tests__/telegram.test.ts` 는 웹 쪽을 같은 표로 잰다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from batch.notify import telegram as tg

뿌리 = Path(__file__).resolve().parent.parent
답안 = json.loads((뿌리 / "tests" / "fixtures" / "telegram_split.json").read_text(encoding="utf-8"))
사례 = 답안["사례"]
웹 = (뿌리 / "web" / "lib" / "telegram.ts").read_text(encoding="utf-8")


def test_표가_비어_있지_않다() -> None:
    """읽어 냈는지 먼저 센다 — 0건이면 아래 전부가 공짜로 통과한다."""
    assert len(사례) >= 7


@pytest.mark.parametrize("행", 사례, ids=[str(r["이름"]) for r in 사례])
def test_파이썬이_표대로_나눈다(행: dict) -> None:
    assert tg.split_message(행["글"], limit=행["한도"]) == 행["조각"]


@pytest.mark.parametrize("행", 사례, ids=[str(r["이름"]) for r in 사례])
def test_글자를_잃지도_더하지도_않는다(행: dict) -> None:
    """무엇이 어떻게 끊기든 **내용은 그대로**여야 한다. 근거에 쓴 수치가 사라지면 안 된다."""
    assert "".join(행["조각"]).replace("\n", "") == 행["글"].replace("\n", "")


@pytest.mark.parametrize(
    "행", [r for r in 사례 if r["줄보존"]], ids=[str(r["이름"]) for r in 사례 if r["줄보존"]]
)
def test_줄이_다_들어가면_이어_붙여_원문이_된다(행: dict) -> None:
    """한 줄도 한도를 넘지 않으면 **줄바꿈 자리에서만** 끊는다.

    한 줄이 통째로 넘칠 때는 없던 자리에서 쪼개므로 이 성질이 성립하지 않는다 —
    그래서 `줄보존` 인 사례만 본다. (2026-09-21: 모든 사례에 걸었다가 틀렸다)
    """
    assert "\n".join(행["조각"]) == 행["글"]


def test_줄보존_사례와_아닌_사례가_둘_다_있다() -> None:
    """위 두 테스트가 각각 **무엇인가를** 재고 있는지. 한쪽이 비면 공짜로 통과한다."""
    보존 = [r["줄보존"] for r in 사례]
    assert any(보존) and not all(보존)


@pytest.mark.parametrize("행", 사례, ids=[str(r["이름"]) for r in 사례])
def test_어느_조각도_한도를_넘지_않는다(행: dict) -> None:
    assert all(len(조각) <= 행["한도"] for 조각 in 행["조각"])


class Test두_언어가_같은_상수를_쓴다:
    """숫자가 어긋나면 표가 같아도 실제 발송이 달라진다."""

    def test_웹도_같은_SAFE_LEN(self) -> None:
        assert f"SAFE_LEN = {tg.SAFE_LEN}" in 웹, f"web/lib/telegram.ts 의 SAFE_LEN 이 {tg.SAFE_LEN} 이 아니다"

    def test_웹도_같은_MAX_LEN(self) -> None:
        assert f"MAX_LEN = {tg.MAX_LEN}" in 웹, f"web/lib/telegram.ts 의 MAX_LEN 이 {tg.MAX_LEN} 이 아니다"

    def test_자르는_길이가_상한보다_작다(self) -> None:
        # 상한이 공식 문서로 확인되지 않았다(docs/data-sources.md 12번). 여유가 없으면 여유가 아니다
        assert tg.SAFE_LEN < tg.MAX_LEN
        assert tg.MAX_LEN - tg.SAFE_LEN >= 100


class Test웹이_실제로_나눠_보낸다:
    """상수만 같고 쓰지 않으면 소용없다. 호출 자리를 본다."""

    def test_보내기가_splitMessage_를_돈다(self) -> None:
        # 2026-09-22 부터 고지를 **나누기 전에** 붙인다 (25.115). 붙인 글을 나눠야
        # 마지막 통에 들어간다 — `splitMessage(text)` 로 되돌아가면 고지가 첫 통에만 남는다
        assert "for (const chunk of splitMessage(withDisclaimer(text)))" in 웹, (
            "나누기를 만들어 두고 쓰지 않거나, 고지를 나눈 **뒤에** 붙이고 있다"
        )

    def test_조각을_보낸다(self) -> None:
        # `text: text` 로 되돌아가면 나누기가 장식이 된다
        assert "text: chunk" in 웹

    @pytest.mark.parametrize(
        "경로",
        ["web/app/api/cron/intraday/route.ts", "web/app/api/cron/health/route.ts"],
    )
    def test_경로가_제_것을_따로_들고_있지_않다(self, 경로: str) -> None:
        """복사본이 남아 있으면 그쪽만 안 나뉜다 — 고쳐도 안 고쳐진 것이 된다."""
        본문 = (뿌리 / 경로).read_text(encoding="utf-8")
        assert "async function sendTelegram" not in 본문, f"{경로} 에 sendTelegram 복사본이 있다"
        assert 'from "@/lib/telegram"' in 본문, f"{경로} 가 공용 발송기를 쓰지 않는다"


def 묶음(줄: str, 건수: int = 50) -> str:
    """`bundleMessage` 가 만드는 모양. 머리 한 줄 + 알림 줄 + 빈 줄 + 고지."""
    return (
        f"장중 알림 {건수}건 (09:35 KST, 지연 시세)\n"
        + "\n".join([줄] * 건수)
        + "\n\n판단 정보일 뿐 자동 매매는 없습니다. 투자 판단의 책임은 본인에게 있습니다."
    )


#: `evaluate()` 가 만드는 가장 긴 줄(권장 매수 구간)에 `bundleMessage` 의 앞머리를 붙인 것.
KR_줄 = "· [국내 09:35] 어떤긴이름주식회사우선주: 권장 매수 구간 진입 1,234,567원 (구간 1,200,000원~1,300,000원)"
#: 미국은 이름이 길다. watchlist 는 `COALESCE(s.name_ko, s.name_en, s.ticker)` 로 영문명을 쓴다
US_줄 = "· [미국 22:35] Taiwan Semiconductor Manufacturing Company Limited: 권장 매수 구간 진입 $1,234.56 (구간 $1,200.00~$1,300.00)"


class Test고친_이유가_실재하는가:
    """**재어 보고 적는다.** 50건은 `flushPending` 의 `LIMIT 50` 이다.

    2026-09-21 실측: 국내만 50건이면 3,923자 — 상한 4,096 은 안 넘지만 **안전선 3,900 은
    넘는다**(173자 여유). 미국 종목명이 섞이면 5,673자로 확실히 넘는다. 즉 국내 전용으로는
    아슬아슬하고, 미국을 켜는 순간 터진다. 여기 숫자가 바뀌면 이 테스트가 알려 준다.
    """

    def test_국내만이어도_안전선을_넘는다(self) -> None:
        길이 = len(묶음(KR_줄))

        assert 길이 > tg.SAFE_LEN, f"국내 묶음이 {길이}자다 — 안전선 아래면 근거를 다시 적어야 한다"

    def test_미국이_섞이면_상한을_넘는다(self) -> None:
        길이 = len(묶음(US_줄))

        assert 길이 > tg.MAX_LEN, f"미국 묶음이 {길이}자다 — 상한 아래면 근거를 다시 적어야 한다"

    @pytest.mark.parametrize("줄", [KR_줄, US_줄], ids=["국내", "미국"])
    def test_나누면_모든_조각이_상한_안에_든다(self, 줄: str) -> None:
        조각들 = tg.split_message(묶음(줄))

        assert len(조각들) >= 2
        assert all(len(c) <= tg.MAX_LEN for c in 조각들)

    def test_고지가_사라지지_않는다(self) -> None:
        """나누기가 마지막 조각을 버리면 고지 문구가 없어진다 (CLAUDE.md 절대 규칙)."""
        조각들 = tg.split_message(묶음(US_줄))

        assert "투자 판단의 책임은 본인에게 있습니다" in 조각들[-1]
