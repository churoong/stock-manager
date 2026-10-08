"""추천의 **재료가 전부 신선도 화면에 있는지** (docs/infra.md 25.78).

`/status` 의 "데이터 신선도" 는 `web/lib/health.ts` 의 `FRESHNESS` 가 정한다.
손으로 적는 목록이라 **빠뜨려도 아무 일이 일어나지 않는다** — 그것이 이 파일이 있는 이유다.

**빠뜨리면 무엇이 나쁜가.** 재료가 몇 달 멈춰도 점수·신호는 **옛 값으로 매일 새로 계산되어**
기준일이 오늘로 찍힌다. 화면은 "점수 2026-09-21" 이라고 싱싱하게 말하는데, 그 점수를 만든
재무는 3월 것일 수 있다. 25.38 에서 미국 데이터가 멈춘 것을 화면이 감췄던 것과 같은 모양이고,
그때는 나라만 갈랐지 **재료 자체가 빠진 것**은 못 봤다.

그래서 `scores`·`signals` 가 **실제로 읽는 표**를 소스에서 긁어 와 대조한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
HEALTH = (뿌리 / "web" / "lib" / "health.ts").read_text(encoding="utf-8")

#: 재료를 읽는 배치. 이 둘이 추천을 만든다
재료를_읽는_배치 = ("batch/jobs/scores.py", "batch/jobs/signals.py")

#: 신선도로 볼 이유가 없는 표. **빠뜨림과 판단을 가른다** (25.76 과 같은 이유)
#:
#: 여기에는 `scores`·`signals` 가 **실제로 `FROM`·`JOIN` 하는** 표만 적는다. 짐작으로
#: 적었다가 `test_예외_목록이_없는_표를_가리키지_않는다` 에 걸렸다 — `factors`·`settings` 는
#: f-string 표 이름과 `db.get_setting` 으로 다루고 `insider_trades` 는 services 를 거친다.
신선도_불필요 = {
    "json_each": "표가 아니라 SQLite 표 값 함수다 — 고른 종목 번호 목록을 펼친다 (signals.load_growth 의 stock_ids, 25.1019)",
    "stocks": "종목 마스터. 날짜가 아니라 상태로 본다",
    "batch_runs": "실행 이력. '최근 배치' 칸이 따로 있다",
    "signal_checks": "판정표. 신호와 같은 실행이다",
    "api_usage": "한도 카운터. 'API 한도' 칸이 따로 있다",
    "sentiment_scores": "`sentiment_kr`·`sentiment_us` 칸이 이미 본다 (docs/infra.md 25.247)",
    "universe_members": "`universe_kr/us` 칸이 이미 본다",
    "prices": "`prices_kr/us` 칸이 이미 본다",
    "scores": "`scores_kr/us` 칸이 이미 본다",
    "signals": "`signals_kr/us` 칸이 이미 본다",
}


def 읽는표() -> set[str]:
    """`FROM x` · `JOIN x` 로 읽는 표 이름."""
    나온것: set[str] = set()
    for 경로 in 재료를_읽는_배치:
        글 = (뿌리 / 경로).read_text(encoding="utf-8")
        나온것 |= {m.lower() for m in re.findall(r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)", 글, re.I)}
        # CTE 이름(`WITH RECURSIVE d(x)`, `w(win)`)은 표가 아니다 (25.865 — 재귀 날짜 걷기)
        나온것 -= {m.lower() for m in re.findall(r"\b(?:WITH(?:\s+RECURSIVE)?|,)\s+([a-z_][a-z0-9_]*)\s*\(", 글, re.I)}
        # `WITH b AS MATERIALIZED (…)` 도 CTE 다 (25.890 — 재무 기준을 종목마다 한 번 고른다)
        나온것 -= {m.lower() for m in re.findall(r"\bWITH\s+([a-z_][a-z0-9_]*)\s+AS\s+(?:NOT\s+)?MATERIALIZED\b", 글, re.I)}
    # SQL 조각이 아닌 말(파이썬 import 의 from 등)과 별칭을 걸러 낸다
    return {t for t in 나온것 if not t.startswith(("batch", "datetime", "typing", "__"))}


읽는것 = 읽는표()
신선도표 = {m.lower() for m in re.findall(r"FROM\s+([a-z_][a-z0-9_]*)", HEALTH, re.I)}


def test_읽어_냈다() -> None:
    """정규식이 빗나가 0개면 아래가 공짜로 통과한다."""
    assert len(읽는것) >= 6, f"읽는 표를 {len(읽는것)}개밖에 못 찾았다"
    assert len(신선도표) >= 8, f"FRESHNESS 에서 {len(신선도표)}개밖에 못 찾았다"


@pytest.mark.parametrize("표", sorted(읽는것))
def test_추천의_재료가_신선도_화면에_있다(표: str) -> None:
    if 표 in 신선도_불필요:
        pytest.skip(f"신선도로 안 본다: {신선도_불필요[표]}")

    assert 표 in 신선도표, (
        f"`{표}` 를 점수·신호가 읽는데 /status 의 데이터 신선도에 없다.\n"
        "재료가 멈춰도 점수는 옛 값으로 매일 새로 계산돼 **기준일이 오늘로 찍힌다** —\n"
        "화면은 싱싱해 보이고 아무도 모른다.\n"
        "신선도로 볼 이유가 없다면 `신선도_불필요` 에 **사유와 함께** 적어라"
    )


def test_안_보는_이유가_적혀_있다() -> None:
    """사유 없는 예외 목록은 빠뜨린 것과 구별이 안 된다 (25.76)."""
    빈것 = [이름 for 이름, 사유 in 신선도_불필요.items() if len(사유.strip()) < 5]

    assert not 빈것, f"사유가 없다: {빈것}"


def test_예외_목록이_없는_표를_가리키지_않는다() -> None:
    """안 읽는 표를 예외에 적어 두면 목록이 낡았다는 뜻이다."""
    없는것 = sorted(set(신선도_불필요) - 읽는것 - 신선도표)

    assert not 없는것, f"점수·신호가 읽지도, 신선도가 보지도 않는 표: {없는것}"


def test_지수와_밴드가_들어_있다() -> None:
    """25.65(베타)·장기 신호의 재료. 이 둘이 멈추면 값이 조용히 빈다."""
    assert "index_prices" in 신선도표
    assert "valuation_bands" in 신선도표


def test_재무는_받아_온_날로_본다() -> None:
    """`report_date` 는 회계연도라 늘 옛날이다. 그것으로 보면 언제나 '낡음' 이다."""
    assert "MAX(f.fetched_at)" in HEALTH
    # 시각 열은 한국 날짜로 바꿔 읽는다 (docs/infra.md 25.240)
    assert "date(MAX(f.fetched_at), '+9 hours')" in HEALTH, "다른 칸과 모양이 달라 보인다"
