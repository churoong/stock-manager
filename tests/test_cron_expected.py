"""**바깥 크론의 기대 주기가 문서와 같은가** (docs/infra.md 25.141).

`/status` 의 "크론 호출" 은 시각만 적어 놓은 줄이었다. `health` 행이 `1970-01-01 · never`
로 앉아 있어도 회색 작은 글씨였는데, 그 한 줄의 뜻은 **"무응답 감시가 아예 안 돈다"** 다 —
아침 리포트가 빠진 날을 아무도 모른다는 뜻이다.

판정을 붙이려면 "얼마마다 불려야 하나" 를 알아야 하고, 그 수의 **정의처는 문서**다
(`docs/intraday.md` 4장의 cron-job.org 등록 표). 사람이 등록할 때 보는 표가 그것이기
때문이다. 화면이 다른 수를 들고 있으면 **등록은 맞는데 화면이 빨개지거나**, 더 나쁘게는
**등록이 틀렸는데 화면이 조용하다.**

25.0 의 「한 규칙이 두 곳에 있다」. 여기서 둘을 묶는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
HEALTH = (뿌리 / "web" / "lib" / "health.ts").read_text(encoding="utf-8")
등록표 = (뿌리 / "docs" / "intraday.md").read_text(encoding="utf-8")
크론길 = 뿌리 / "web" / "app" / "api" / "cron"

#: 문서 표의 **작업 이름** → 화면의 (job, market). 표는 사람이 읽는 말로 적혀 있다
문서행 = {
    "국내 장중": ("intraday", "KR"),
    "미국 장중": ("intraday", "US"),
    "무응답 감시": ("health", "ALL"),
    "미국 뉴스": ("news", "US"),
    "국내 뉴스": ("news", "KR"),
}


def 화면표() -> dict[tuple[str, str], int]:
    """`CRON_EXPECTED` 의 (job, market) → everyMinutes."""
    몸통 = HEALTH.split("export const CRON_EXPECTED", 1)[1].split(" = [", 1)[1].split("\n];", 1)[0]
    깨끗 = "\n".join(줄 for 줄 in 몸통.splitlines() if not 줄.strip().startswith("//"))
    나온것 = {}
    for m in re.finditer(r'job: "(\w+)", market: "(\w+)"', 깨끗):
        뒤 = 깨끗[m.end() : m.end() + 400]
        분 = re.search(r"everyMinutes: (\d+)", 뒤)
        assert 분, f"{m.group(1)}/{m.group(2)} 의 everyMinutes 를 못 읽었다"
        나온것[(m.group(1), m.group(2))] = int(분.group(1))
    return 나온것


def 문서표() -> dict[tuple[str, str], int]:
    """등록 표에서 **매 N분** 이 적힌 줄만."""
    나온것 = {}
    for 줄 in 등록표.splitlines():
        if not 줄.startswith("| "):
            continue
        칸 = [c.strip() for c in 줄.strip("|").split("|")]
        if len(칸) < 3 or 칸[0] not in 문서행:
            continue
        분 = re.search(r"매\s*\*{0,2}(\d+)분", 칸[2])
        if 분:
            나온것[문서행[칸[0]]] = int(분.group(1))
    return 나온것


def 기록하는_것() -> set[str]:
    """경로가 실제로 `cron_heartbeats` 에 적는 job 이름."""
    이름 = set()
    for 길 in sorted(크론길.rglob("route.ts")):
        글 = 길.read_text(encoding="utf-8")
        이름 |= set(re.findall(r'recordHeartbeat\(\s*"(\w+)"', 글))
    return 이름


화면 = 화면표()
문서 = 문서표()


def test_읽어_냈다() -> None:
    """정규식이 빗나가 비면 아래가 전부 공짜로 통과한다."""
    assert len(화면) >= 5, f"CRON_EXPECTED 를 {len(화면)}줄밖에 못 읽었다"
    assert len(문서) >= 5, f"등록 표에서 {len(문서)}줄밖에 못 읽었다: {문서}"


@pytest.mark.parametrize("키", sorted(화면))
def test_화면의_주기가_등록_표와_같다(키: tuple[str, str]) -> None:
    """**정의처는 문서다.** 등록 주기를 바꾸면 여기가 깨진다.

    안 깨지면 어떻게 되나: 1분으로 등록해 두고 화면이 60분으로 재면 **멈춰도 조용하다.**
    반대로 60분으로 등록하고 화면이 1분으로 재면 **늘 빨갛다** — 그러면 사람이 이 화면을 닫는다.
    """
    assert 키 in 문서, (
        f"{키} 가 docs/intraday.md 4장 등록 표에 없다. "
        "사람이 등록할 때 보는 표에 없으면 등록되지 않는다"
    )
    assert 화면[키] == 문서[키], (
        f"{키} 의 주기가 화면은 {화면[키]}분, 문서는 {문서[키]}분이다.\n"
        "문서가 정의처다 — 등록을 바꿨으면 web/lib/health.ts 의 `CRON_EXPECTED` 도 고쳐라"
    )


def test_문서에만_있는_줄이_없다() -> None:
    """표에 적어 두고 화면이 모르면 그 크론은 **아무도 안 본다** (25.139 와 같은 모양)."""
    빠진것 = sorted(set(문서) - set(화면))

    assert not 빠진것, f"등록 표에 있는데 화면이 판정하지 않는다: {빠진것}"


def test_기록을_남기는_경로가_모두_표에_있다() -> None:
    """새 크론 경로를 만들면서 기대 주기를 안 적으면 여기서 걸린다."""
    이름 = 기록하는_것()
    assert 이름, "recordHeartbeat 호출을 하나도 못 찾았다 — 경로 모양이 바뀌었나"

    모르는것 = sorted(이름 - {job for job, _ in 화면})
    assert not 모르는것, (
        f"`cron_heartbeats` 에 적는데 기대 주기가 없는 크론: {모르는것}.\n"
        "`CRON_EXPECTED` 와 docs/intraday.md 4장 표에 함께 적어라"
    )


def test_안_불릴_때_무엇이_멈추는지_적혀_있다() -> None:
    """**판정에는 무게가 있어야 한다.** "늦음" 만 적으면 무엇을 잃는지 모른다."""
    몸통 = HEALTH.split("export const CRON_EXPECTED", 1)[1].split(" = [", 1)[1].split("\n];", 1)[0]
    사유 = re.findall(r'stakes: "([^"]+)"', 몸통)

    assert len(사유) == len(화면), f"stakes 가 {len(사유)}개인데 줄은 {len(화면)}개다"
    assert all(len(s) > 20 for s in 사유), f"사유가 너무 짧다: {[s for s in 사유 if len(s) <= 20]}"


def test_사용자_안내가_같은_수를_말한다() -> None:
    """사용자는 `docs/todo-user.md` 를 보고 등록한다.

    2026-09-23 까지 그 파일은 **"news 10분"** 이라고 적고 있었다 — 정의처(sentiment.md)는
    1분이다. 미국 뉴스는 **한 호출에 한 종목**이라 10분이면 하루 144종목, 150종목을
    한 바퀴 도는 데 하루가 넘는다. 잘못 적힌 안내는 잘못된 등록을 만든다.
    """
    글 = (뿌리 / "docs" / "todo-user.md").read_text(encoding="utf-8")

    assert "news 10분" not in 글, "todo-user 가 옛 주기를 말한다 (docs/infra.md 25.141)"
    assert re.search(r"news\s*매\s*\*{0,2}1분", 글), "미국 뉴스 주기가 안내에 없다"
