"""점수를 고를 때 **계산 판(calc_version)까지** 보는가 (docs/infra.md 25.91).

`scores`·`factors`·`signals` 의 유니크 키에는 `calc_version` 이 들어 있고
(migrations/0007_scores.sql · 0008_signals.sql), 적재는 **같은 판만 지운다**
(`jobs/scores.clear_statement`: `DELETE ... WHERE as_of_date = ? AND calc_version = ?`).

그래서 계산식을 올린 날(예: CALC_VERSION 3→4)에는 **같은 기준일에 두 행이 남는다.**
그때 `as_of_date = (SELECT MAX(as_of_date) ...)` 로만 고르면 한 종목이 **두 번** 걸린다.

2026-09-21 에 실제로 그랬다. 결과는 조용했다 —
  · 신호가 같은 종목을 두 번 판정하고, 둘의 밸류 점수가 다르다(옛 판에는 배당이 없다)
  · 신호 충돌 키에 calc_version 이 있어 나중 것이 앞을 덮는데 **그 순서가 정해져 있지 않다**
  · 근거표에 적힌 점수와 종목 상세 화면의 점수가 **다른 숫자**가 된다
    (CLAUDE.md: 근거의 모든 수치는 어디서 왔는지 확인할 수 있어야 한다)

`web/lib/stockDetail.ts` 는 처음부터 `ORDER BY as_of_date DESC, calc_version DESC` 로
골랐다. 나머지가 그것을 안 따르고 있었다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent

#: 훑을 곳
훑을곳 = [뿌리 / "batch", 뿌리 / "web" / "lib", 뿌리 / "web" / "app", 뿌리 / "scripts"]

#: **지금 깨진 표.** 계산 판을 올려 같은 기준일에 두 행이 남는다.
#: `scores`·`factors` 는 `scoring.CALC_VERSION` 3→4 (25.91),
#: `performance_metrics` 는 `metrics.CALC_VERSION` 1→2 (2026-09-22, 25.102)
깨진_표 = ("scores", "factors", "performance_metrics")

#: **판을 몰라도 되는 자리.** 사유와 함께 적는다 — 빠뜨림과 판단을 가른다
판이_필요_없는_곳 = {
    "scripts/db_status.py": "화면에 **날짜만** 찍는다(점수기준일). 행이 둘이어도 MAX 는 같다",
    "scripts/catchup_report.py": "`COUNT(DISTINCT stock_id)` 라 행이 둘이어도 수가 같다",
    "web/lib/health.ts": "표의 **최신 날짜**만 본다(신선도). 판과 무관하다",
    "web/lib/stockDetail.ts": "`ORDER BY window, calc_version DESC` 뒤 `firstBy(…, 'window')` 라 **이미 높은 판이 이긴다**",
}

#: **아직 안 깨진 표.** 각 표를 쓰는 계산 판이 아직 1 이라 한 기준일에 한 행뿐이다.
#: 그 판을 올리는 순간 `깨진_표` 와 똑같아진다 — 아래 `test_잠재된_표의_판이_그대로다` 가 그때 문다
잠재된_표 = {
    "signals": ("batch.services.signals", 1),
    "signal_checks": ("batch.services.signals", 1),
}


def 파일들() -> list[Path]:
    나온것: list[Path] = []
    for 밑 in 훑을곳:
        for 확장 in ("*.py", "*.ts", "*.tsx"):
            나온것 += [p for p in 밑.rglob(확장) if "__pycache__" not in str(p)]
    return sorted(나온것)


def 걸린것(표들) -> list[str]:
    꼴 = re.compile(
        r"MAX\(\s*(?:\w+\.)?as_of_date\s*\)\s*FROM\s+(" + "|".join(표들) + r")\b", re.I
    )
    나온것 = []
    for 길 in 파일들():
        글 = 길.read_text(encoding="utf-8", errors="replace")
        for m in 꼴.finditer(글):
            줄번호 = 글[: m.start()].count("\n") + 1
            # 같은 문장 안에서 calc_version 을 함께 보면 괜찮다. 앞뒤 400자를 본다
            둘레 = 글[max(0, m.start() - 400) : m.end() + 400]
            if "calc_version" in 둘레:
                continue
            상대 = str(길.relative_to(뿌리))
            if 상대 in 판이_필요_없는_곳:
                continue
            나온것.append(f"{길.relative_to(뿌리)}:{줄번호}  {m.group(0)}")
    return 나온것


def test_훑어_냈다() -> None:
    """0개면 아래가 공짜로 통과한다."""
    assert len(파일들()) >= 100
    # 꼴 자체가 안 맞으면 아래가 전부 공짜로 통과한다. 잠재된 쪽에서 **실제로 잡히는지** 본다
    assert 걸린것(잠재된_표), "정규식이 아무것도 못 잡는다 — 모양이 바뀌었나"


def test_판이_올라간_표를_판_없이_고르지_않는다() -> None:
    """**지금 깨져 있는 것.** scores·factors 는 같은 기준일에 v3·v4 두 행이 있다."""
    걸림 = 걸린것(깨진_표)

    assert not 걸림, (
        "계산 판(calc_version)을 안 보고 '가장 최근' 점수를 고른다. 판을 올린 날 같은\n"
        "기준일에 두 행이 남아 **한 종목이 두 번** 걸린다:\n  " + "\n  ".join(걸림) + "\n\n"
        "web/lib/stockDetail.ts 처럼 `ORDER BY as_of_date DESC, calc_version DESC LIMIT 1` 로 고른다"
    )


def test_잠재된_표의_판이_그대로다() -> None:
    """**빠뜨림과 판단을 가른다.**

    `signals`·`performance_metrics` 를 읽는 곳들도 판을 안 본다. 지금은 괜찮다 —
    그 표를 쓰는 계산 판이 아직 1 이라 한 기준일에 한 행뿐이다.

    **그 판을 올리는 순간 scores 와 똑같은 일이 난다.** 그래서 지금 고치는 대신
    여기에 못 박는다: 판이 움직이면 이 테스트가 먼저 물고, 그때 읽는 쪽을 함께 고친다.
    (지금 고치지 않는 이유: 잠재 위험 하나 때문에 질의 열 곳을 건드리는 것이
     지금 고치는 것보다 위험하다. 고치는 방법은 위 두 곳에 이미 적혀 있다.)
    """
    import importlib

    어긋남 = []
    for 표, (모듈이름, 적어둔판) in 잠재된_표.items():
        실제 = importlib.import_module(모듈이름).CALC_VERSION
        if 실제 != 적어둔판:
            어긋남.append(f"{표}: {모듈이름}.CALC_VERSION 이 {적어둔판} → {실제} 로 바뀌었다")

    assert not 어긋남, (
        "\n".join(어긋남) + "\n\n"
        "이 표를 읽는 곳들이 calc_version 을 안 본다. 판을 올리면 같은 기준일에 두 행이 남아\n"
        "**한 종목이 두 번** 걸린다(docs/infra.md 25.91). 읽는 쪽을 먼저 고치고 이 목록을 갱신하라:\n  "
        + "\n  ".join(걸린것(잠재된_표))
    )


class Test고친_두_곳:
    """돌아가지 않게 못 박는다."""

    #: 2026-09-21 에 고친 자리. **한 가지 꼴로 통일했다** —
    #: 날짜 고르는 규칙은 그대로 두고 판만 한 겹 더 건다. 그래야 각 자리가
    #: 원래 보던 날짜 규칙(아침 리포트는 `<= 신호일`, 장중은 시장 전체 최신)을 잃지 않는다
    고친곳 = [
        "batch/jobs/signals.py",
        "batch/jobs/daily.py",
        "batch/jobs/monitor_targets.py",
        "web/lib/recommend.ts",
    ]

    @pytest.mark.parametrize("경로", 고친곳)
    def test_판을_함께_본다(self, 경로: str) -> None:
        글 = (뿌리 / 경로).read_text(encoding="utf-8")

        assert "MAX(c.calc_version)" in 글, f"{경로} 가 판을 안 보고 최신 점수를 고른다"

    def test_종목_상세는_처음부터_제대로_골랐다(self) -> None:
        """**따라야 할 본보기다.** 한 행만 가져오는 자리라 ORDER BY 로 고른다."""
        글 = (뿌리 / "web" / "lib" / "stockDetail.ts").read_text(encoding="utf-8")

        assert "as_of_date DESC, calc_version DESC" in 글

    def test_사유가_적혀_있다(self) -> None:
        """사유 없는 예외 목록은 빠뜨린 것과 구별이 안 된다."""
        빈것 = [k for k, v in 판이_필요_없는_곳.items() if len(v.strip()) < 5]

        assert not 빈것, f"사유가 없다: {빈것}"


def test_적재가_같은_판만_지운다는_사실을_못_박는다() -> None:
    """**이 성질이 위 문제의 뿌리다.** 다른 판의 옛 행을 남기는 것은 의도다
    (docs/factors.md: "과거 행은 그대로 두고 새 버전으로 쌓는다"). 그러니 **읽는 쪽**이 골라야 한다.
    """
    글 = (뿌리 / "batch" / "jobs" / "scores.py").read_text(encoding="utf-8")

    assert "WHERE as_of_date = ? AND calc_version = ?" in 글, (
        "적재가 모든 판을 지우게 바뀌었다면 이 테스트의 전제가 달라졌다 — 다시 따져라"
    )


#: **두 번째 꼴** (docs/infra.md 25.210). `MAX(as_of_date)` 가 아니라 `ORDER BY as_of_date DESC LIMIT 1` 로
#: "가장 최근 한 행" 을 고르는 꼴이다. 위 그물은 이것을 못 봤다 — 매도 플래그의 지금 점수(25.209)와
#: 매매 입력의 점수 스냅샷(`web/lib/portfolio.SNAPSHOT_AT`)이 이 꼴로 판을 안 보고 있었다
두번째_꼴 = re.compile(
    r"FROM\s+(?:" + "|".join(깨진_표) + r")\b(?:(?!FROM\s)[\s\S]){0,300}?ORDER BY\s+(?:\w+\.)?as_of_date\s+DESC"
    r"(?P<뒤>[^`\"\n]*)",
    re.I,
)


def 최근_한_행_꼴() -> list[str]:
    나온것 = []
    for 길 in 파일들():
        상대 = str(길.relative_to(뿌리))
        if 상대 in 판이_필요_없는_곳:
            continue
        글 = 길.read_text(encoding="utf-8", errors="replace")
        for m in 두번째_꼴.finditer(글):
            if "calc_version" in m.group(0):
                continue
            나온것.append(f"{상대}:{글[: m.start()].count(chr(10)) + 1}")
    return 나온것


def test_최근_한_행_꼴도_판을_본다() -> None:
    걸림 = 최근_한_행_꼴()

    assert not 걸림, (
        "`ORDER BY as_of_date DESC` 로 가장 최근 행을 고르면서 판을 안 본다 — 같은 날 판이 둘이면 아무거나 집는다:\n  "
        + "\n  ".join(걸림)
        + "\n`ORDER BY as_of_date DESC, calc_version DESC` 로 고른다"
    )


def test_두번째_꼴이_실제로_문다() -> None:
    """미끼. 판을 뺀 문장을 꼴이 잡는지 본다 — 못 잡으면 위 검사가 공짜로 통과한다."""
    나쁜예 = "SELECT total_score FROM scores sc WHERE sc.stock_id = ? ORDER BY sc.as_of_date DESC LIMIT 1"
    좋은예 = "SELECT total_score FROM scores sc WHERE sc.stock_id = ? ORDER BY sc.as_of_date DESC, sc.calc_version DESC LIMIT 1"
    assert 두번째_꼴.search(나쁜예) and "calc_version" not in 두번째_꼴.search(나쁜예).group(0)  # type: ignore[union-attr]
    assert "calc_version" in 두번째_꼴.search(좋은예).group(0)  # type: ignore[union-attr]
