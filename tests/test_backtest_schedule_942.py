"""기본 백테스트 월 1회 예약 (docs/infra.md 25.942). 발굴 루프의 판정이 "그 달 첫 기본 실행" 만 세는데 예약이 없어 영영 쌓이지 않았다."""

from __future__ import annotations

from pathlib import Path

import yaml

뿌리 = Path(__file__).resolve().parent.parent
워크플로 = yaml.safe_load((뿌리 / ".github/workflows/backtest.yml").read_text(encoding="utf-8"))
# PyYAML 은 `on` 을 True 로 읽는다
트리거 = 워크플로.get("on") or 워크플로.get(True)


def test_매달_국내_미국_두_예약이_있다() -> None:
    크론들 = [c["cron"] for c in 트리거["schedule"]]
    assert len(크론들) == 2
    for 크론 in 크론들:
        분, 시, 일, 월, 요일 = 크론.split()
        assert (일, 월, 요일) == ("3", "*", "*"), f"매달 3일이어야 한다 (1·2일은 전체 백업·ETF 가 읽기를 몰아 쓴다): {크론}"


def test_미국_예약은_그_크론으로만_가른다() -> None:
    """입력이 없는 예약 실행은 cron 줄로 시장을 정한다 — 둘째 크론이 US, 나머지는 기본 KR."""
    env = 워크플로["jobs"]["run"]["env"]
    us_cron = 트리거["schedule"][1]["cron"]
    assert f"github.event.schedule == '{us_cron}' && 'US'" in env["MARKET"]
    # 예약 실행은 기간·상위 N·비교 깃발 입력이 비어 기본값으로 떨어진다 → `params.default = true`
    assert env["YEARS"].endswith("|| '5' }}") and env["TOP_N"].endswith("|| '20' }}")
    assert "no_pit_universe" in env["NO_PIT"] and "trend_filter" in env["TREND"]


def test_문서가_예약을_안다() -> None:
    assert "매달 3일" in (뿌리 / "docs/backtest.md").read_text(encoding="utf-8")
    assert "매달 3일" in (뿌리 / "docs/factors.md").read_text(encoding="utf-8")
