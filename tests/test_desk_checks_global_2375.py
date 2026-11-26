"""운용 화면 '확인할 일' — 두 시장이 함께 보는 사실만 표지 (docs/infra.md 25.2375, 결함 루프 2026-11-26).

화면(`web/lib/desk.ts allChecks`)이 글만으로 두 시장 줄을 합쳐, 시장마다 따로 난 "KIS모의투자 거부 1건"·같은 토큰
오류 글의 미국 줄이 사라졌다. 이제 `kind: global`(멈춤 스위치)만 한 번, 나머지는 시장마다.
"""

from __future__ import annotations

from batch.services import desk


def test_멈춤만_global() -> None:
    줄 = desk.checks(halted=True, paper={}, broker={"error": "KisFailed: 토큰 발급 실패", "sent": {"rejected": 1}},
                     recon_bad=[], warnings=0)  # fmt: skip
    전역 = [c for c in 줄 if c.get("kind") == "global"]
    assert len(전역) == 1 and 전역[0]["text"].startswith("멈춤 스위치")
    # 나머지는 KIS 기록에서 난 줄(25.2380 `kind: kis`) — 전역이 아니다
    assert all(c.get("kind") == "kis" for c in 줄 if not c["text"].startswith("멈춤 스위치"))
