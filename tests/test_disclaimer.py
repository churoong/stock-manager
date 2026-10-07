"""책임 고지와 색인 차단 — CLAUDE.md 의 **절대 규칙**을 테스트로 잠근다.

> 모든 화면 하단과 텔레그램 리포트 끝에 "투자 판단의 책임은 본인에게 있습니다" 고지
> 웹앱은 인증된 본인 계정만 접근 가능 … 검색엔진 색인 차단

**왜 이 테스트가 생겼나.** 2026-09-20 에 절대 규칙들이 실제로 테스트로 고정돼 있는지
훑어봤더니, 레버리지·인버스 제외는 잠겨 있는데 **이 둘은 코드에만 있고 테스트가 없었다.**

이런 규칙은 **조용히 깨진다.** 아무도 매일 푸터를 읽지 않고 `robots.txt` 를 열어 보지 않는다.
리팩터링 한 번에 사라져도 몇 달을 모른다 — 그러다 검색 결과에 뜨거나, 고지 없는 추천이
나간다. 둘 다 되돌릴 수 없는 종류다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from batch import config
from batch.notify import telegram

ROOT = Path(__file__).resolve().parent.parent


class Test텔레그램_고지:
    """`telegram.send()` 가 한 곳에서 붙인다. 부르는 쪽마다 적게 하면 언젠가 빠뜨린다."""

    @staticmethod
    def _보내고_받기(monkeypatch: pytest.MonkeyPatch, text: str, 켬: bool = True) -> list[str]:
        보낸것: list[str] = []
        # 텔레그램 고지는 2026-10-02 사용자 지시로 꺼졌다(25.878). 아래 검사들은 **켰을 때**의 규칙을 지킨다
        monkeypatch.setattr(config, "TELEGRAM_DISCLAIMER", 켬)
        monkeypatch.setattr(telegram, "resolve_chat_id", lambda: "1")
        monkeypatch.setattr(
            telegram, "_call", lambda name, body: 보낸것.append(body["text"]) or {"result": {"message_id": 1}}
        )
        telegram.send(text)
        return 보낸것

    def test_기본은_텔레그램에_붙이지_않는다_25_878(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """2026-10-02 사용자 지시: "텔레그램 메시지에 '투자 판단의 책임은 본인에게 있습니다.' 이거 다 빼"."""
        monkeypatch.undo()
        assert config.TELEGRAM_DISCLAIMER is False
        보낸것 = self._보내고_받기(monkeypatch, "오늘의 추천", 켬=False)
        assert config.DISCLAIMER not in "".join(보낸것)

    def test_없으면_붙인다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        보낸것 = self._보내고_받기(monkeypatch, "오늘의 추천")
        assert config.DISCLAIMER in "".join(보낸것)

    def test_이미_있으면_두_번_붙이지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        보낸것 = self._보내고_받기(monkeypatch, f"오늘의 추천\n\n{config.DISCLAIMER}")
        assert "".join(보낸것).count(config.DISCLAIMER) == 1

    def test_길어서_나눠져도_마지막_통에_있다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """규칙은 "리포트 **끝에**" 다. 나뉘어 첫 통에만 있으면 지킨 것이 아니다."""
        긴글 = "\n".join(f"{i}번 종목 추천 근거 문장" for i in range(1, 2000))
        보낸것 = self._보내고_받기(monkeypatch, 긴글)
        assert len(보낸것) > 1, "이 테스트는 나뉘는 길이라야 뜻이 있다"
        assert config.DISCLAIMER in 보낸것[-1], f"마지막 통에 고지가 없다:\n…{보낸것[-1][-120:]}"

    def test_고지_문구_자체가_바뀌지_않았다(self) -> None:
        """CLAUDE.md 에 적힌 문장 그대로여야 한다. 말이 바뀌면 규칙이 바뀐 것이다."""
        assert config.DISCLAIMER == "투자 판단의 책임은 본인에게 있습니다."

    def test_고지를_끌_수_있는_길을_아무도_쓰지_않는다(self) -> None:
        """`with_disclaimer=False` 는 기본값을 위해 있는 매개변수다. 실제로 끄는 호출이 생기면
        그 순간 규칙이 깨진다 — 정의부 말고 **부르는 쪽**에 나타나면 잡는다."""
        쓰는곳 = [
            f"{p}:{i}"
            for p in list(ROOT.glob("batch/**/*.py")) + list(ROOT.glob("scripts/*.py"))
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if "with_disclaimer" in line and "def send(" not in line and not line.strip().startswith("if with_disclaimer")
        ]
        assert not 쓰는곳, f"고지를 끄는 호출이 있다: {쓰는곳}"


class Test웹_화면:
    """**여기서 증명할 수 있는 것은 "설정이 소스에 있다" 까지다.** 실제로 그려지는지는
    웹 테스트(`web/__tests__`)와 배포된 화면이 본다. 그래도 이 줄이 통째로 사라지는 사고는
    잡는다 — 이 저장소에서 파이썬 테스트는 언제나 돌지만 웹 테스트는 `npm ci` 가 있어야 돈다.
    """

    def test_푸터에_고지가_있다(self) -> None:
        """2026-09-22 부터 푸터도 `web/lib/notice.DISCLAIMER` 를 쓴다 (25.115).

        글자를 찾는 대신 **그 상수를 쓰는지**를 본다. 사본을 없앴으니 글자로 찾으면
        "사라졌다" 고 잘못 말한다.
        """
        본문 = (ROOT / "web/components/Footer.tsx").read_text(encoding="utf-8")
        assert "{DISCLAIMER}" in 본문
        assert 'from "@/lib/notice"' in 본문

    def test_FRED_고지가_푸터에_있다(self) -> None:
        """이용약관상 **의무**다. 지금은 FRED 를 데이터 소스로 쓰지 않지만
        (`batch/sources/` 에 없다) 미국 무위험수익률을 붙이는 순간 필요해진다.
        먼저 넣어 두고 잠가 둔다 — 나중에 넣으려면 그때 아무도 기억하지 못한다."""
        본문 = (ROOT / "web/components/Footer.tsx").read_text(encoding="utf-8")
        assert "not endorsed or certified by the" in 본문
        assert "FRED" in 본문

    def test_FRED_고지_문구가_약관_그대로다(self) -> None:
        """한 글자도 바꾸면 안 되는 종류다. 배치·화면이 같은 상수를 쓴다."""
        assert config.FRED_NOTICE.startswith(
            "This product uses the FRED® API but is not endorsed or certified "
        )
        assert "Federal Reserve Bank of St. Louis" in config.FRED_NOTICE

    def test_색인을_세_겹으로_막는다(self) -> None:
        """한 곳만 막으면 한 곳만 풀려도 노출된다. 세 곳 모두 있어야 한다."""
        확인 = {
            "web/app/robots.ts": r"disallow",
            "web/app/layout.tsx": r"robots:\s*\{\s*index:\s*false",
            "web/next.config.ts": r"X-Robots-Tag",
        }
        빠진것 = [
            path
            for path, 무늬 in 확인.items()
            if not re.search(무늬, (ROOT / path).read_text(encoding="utf-8"), re.IGNORECASE)
        ]
        assert not 빠진것, f"색인 차단이 빠진 곳: {빠진것}"


class Test웹도_같은_글자로_한_곳에서:
    """웹이 **부르는 쪽마다** 고지를 적고 있었다 (2026-09-22, docs/infra.md 25.115).

    파이썬은 `notify/telegram.send` 가 보내기 직전에 한 번 붙인다. 웹은 그러지 않아서
    같은 문장이 다섯 벌 손으로 적혀 있었고 **무응답 알림 하나는 아예 안 붙이고 있었다.**
    규칙을 부르는 쪽마다 지키게 하면 언젠가 빠뜨린다 — 실제로 빠뜨렸다.
    """

    웹 = ROOT / "web"

    def test_글자가_두_언어에서_같다(self) -> None:
        본문 = (self.웹 / "lib" / "notice.ts").read_text(encoding="utf-8")
        m = re.search(r'export const DISCLAIMER = "([^"]+)"', 본문)
        assert m, "web/lib/notice.ts 의 DISCLAIMER 를 못 읽었다"
        assert m.group(1) == config.DISCLAIMER, (
            f"고지 문구가 갈라졌다: 파이썬 {config.DISCLAIMER!r} · 웹 {m.group(1)!r}"
        )

    def test_보내는_쪽이_붙인다(self) -> None:
        """부르는 쪽이 아니라 `sendTelegram` 이 붙여야 새 경로도 저절로 지켜진다."""
        본문 = (self.웹 / "lib" / "telegram.ts").read_text(encoding="utf-8")
        보내는곳 = 본문.split("export async function sendTelegram", 1)[1]
        assert "withDisclaimer(text)" in 보내는곳, "보내기 직전에 붙이지 않는다"
        assert "splitMessage(withDisclaimer(" in 보내는곳, (
            "나누기 **전에** 붙여야 마지막 통에 들어간다"
        )

    def test_문장을_손으로_다시_적은_곳이_없다(self) -> None:
        """**글자가 아니라 사본의 수를 본다.** 상수를 만들어 두고 옆에 또 적으면 뜻이 없다."""
        베낀곳 = []
        for path in sorted(self.웹.rglob("*.ts*")):
            숨길곳 = {"node_modules", "__tests__", "coverage", ".next"}
            if 숨길곳 & set(path.parts) or path.name == "notice.ts":
                continue
            if config.DISCLAIMER in path.read_text(encoding="utf-8"):
                베낀곳.append(str(path.relative_to(ROOT)))
        assert not 베낀곳, (
            "고지 문장을 손으로 적은 곳이 있다. web/lib/notice.ts 의 DISCLAIMER 를 쓴다:\n  "
            + "\n  ".join(베낀곳)
        )
