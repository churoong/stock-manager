"""한도 카운터의 이름이 파이썬과 웹에서 같은가 (docs/infra.md 25.105).

`api_usage.api_name` 이 갈리면 하루 한도를 보는 카운터가 **둘로 쪼개져** 한쪽에 쓴 만큼은
한도 계산에 안 보인다. 2026-09-21 에 DART 를 `dart` 와 `dart_opendart` 두 이름으로 세다가
겪었다(25.68). 그때는 **파이썬 안에서** 갈렸고, 이번에는 웹이 같은 표에 쓰기 시작했다 —
**언어가 갈렸다.** 글자 하나 다르면 아무도 모르므로 대조를 기계에 맡긴다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from batch.core.db import API_NAMES

WEB = Path(__file__).resolve().parents[1] / "web" / "lib" / "apiUsage.ts"


def _웹_상수(이름: str) -> str:
    본문 = WEB.read_text(encoding="utf-8")
    m = re.search(rf'export const {이름} = "([^"]+)"', 본문)
    assert m, f"web/lib/apiUsage.ts 에서 {이름} 을 못 읽었다"
    return m.group(1)


def test_웹이_쓰는_이름이_파이썬_목록에_있다() -> None:
    assert _웹_상수("DART_API_NAME") in API_NAMES


def test_웹이_같은_한도를_본다() -> None:
    """한도가 다르면 80% 경고가 두 시점에 온다 — 어느 쪽이 맞는지 아무도 모른다.

    **20,000 을 여기에 다시 적지 않는다** (2026-09-22). 전에는 웹의 값을 숫자 리터럴과
    맞춰 봤다 — `batch/sources/dart.DAILY_LIMIT` 을 바꿔도 이 검사는 통과하고 웹만 옛 값에
    남는다. 대조의 한쪽 끝이 **정의처**여야 대조가 뜻을 갖는다 (25.0 「테스트가 자료를 손으로 베낀다」).
    """
    from batch.sources.dart import DAILY_LIMIT

    본문 = WEB.read_text(encoding="utf-8")
    m = re.search(r"export const DART_DAILY_LIMIT = ([0-9_]+);", 본문)
    assert m, "DART_DAILY_LIMIT 을 못 읽었다"
    assert int(m.group(1).replace("_", "")) == DAILY_LIMIT, (
        f"웹은 {m.group(1)}, 배치는 {DAILY_LIMIT:,} 을 본다 (docs/data-sources.md 의 일일 20,000건)"
    )


@pytest.mark.parametrize(
    ("call_count", "limit_value", "기대"),
    [(0, 20_000, "ok"), (15_999, 20_000, "ok"), (16_000, 20_000, "warn"), (20_000, 20_000, "blocked"), (5, None, "unknown")],
)
def test_판정식이_웹과_같은_답을_낸다(call_count: int, limit_value: int | None, 기대: str) -> None:
    """`web/lib/limits.ts` 의 `usageTone` 과 같은 답이어야 한다.

    웹 쪽은 `web/__tests__/apiUsage.test.ts` 가 같은 표를 본다. 두 테스트가 같은 자리를
    집어야 어긋남이 드러난다 — 한쪽만 고치면 다른 쪽이 빨개진다.
    """
    from batch.core.db import evaluate_limit_state

    assert evaluate_limit_state(call_count, limit_value, 80) == 기대


WEB_USAGE = Path(__file__).resolve().parents[1] / "web" / "lib" / "webUsage.ts"


def _웹_수(파일: Path, 이름: str) -> int:
    본문 = 파일.read_text(encoding="utf-8")
    m = re.search(rf"export const {이름} = ([0-9_]+);", 본문)
    assert m, f"{파일.name} 에서 {이름} 을 못 읽었다"
    return int(m.group(1).replace("_", ""))


def _웹_글자(파일: Path, 이름: str) -> str:
    본문 = 파일.read_text(encoding="utf-8")
    m = re.search(rf'export const {이름} = "([^"]+)"', 본문)
    assert m, f"{파일.name} 에서 {이름} 을 못 읽었다"
    return m.group(1)


class Test웹도_D1_행을_센다:
    """2026-09-22 에 웹이 같은 하루 예산을 세기 시작했다 (docs/infra.md 25.124).

    **한 표의 한 행을 둘이 갱신한다.** 이름이나 한도가 갈리면 한쪽 몫이 사라지거나
    게이지가 두 시점에 경고한다 — 25.68 에서 겪은 그 모양이다.
    """

    def test_이름이_파이썬_목록에_있다(self) -> None:
        for 이름 in ("D1_WRITES_NAME", "D1_READS_NAME"):
            assert _웹_글자(WEB_USAGE, 이름) in API_NAMES

    def test_한도가_같다(self) -> None:
        from batch.core.db import D1_DAILY_READ_LIMIT, D1_DAILY_WRITE_LIMIT

        assert _웹_수(WEB_USAGE, "D1_DAILY_WRITE_LIMIT") == D1_DAILY_WRITE_LIMIT
        assert _웹_수(WEB_USAGE, "D1_DAILY_READ_LIMIT") == D1_DAILY_READ_LIMIT

    def test_쓰기는_모아_두지_않는다(self) -> None:
        """웹의 쓰기는 드물고 작다. 모아 두면 인스턴스가 사라질 때 그만큼 **덜 센다** —
        적게 보이는 카운터는 없느니만 못하다(infra 23절). 읽기는 양이 커서 문턱을 둔다."""
        assert _웹_수(WEB_USAGE, "WRITE_FLUSH_ROWS") == 1
        assert _웹_수(WEB_USAGE, "READ_FLUSH_ROWS") > 1
