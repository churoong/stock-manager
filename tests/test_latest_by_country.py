""""가장 최근 것" 을 나라 구분 없이 잡지 않는가 (파이썬·웹 둘 다).

**이미 두 번 겪었다.**

1. 2026-09-17, `universe_members`: 나라 구분 없이 `MAX(snapshot_date)` 를 잡았다.
   국내 스냅샷이 09-16, 미국이 09-15 이면 **늦은 쪽만 남고 미국 편입 종목이
   metrics·scores·signals 에서 통째로 사라졌다** (`db.latest_snapshot_sql` 주석).
2. 2026-09-21, `signals`: `batch/jobs/stress.py` 가 같은 실수를 하고 있었다.
   신호는 `signals.run(market)` 으로 나라마다 따로 계산되고, 국내·미국 배치가 도는 UTC
   날짜가 달라 `as_of_date` 가 어긋난다. 늦게 돈 나라의 날짜가 잡히면 다른 나라는 0건이 되고,
   스트레스 배치가 **"신호가 없습니다. 매수 신호 배치를 먼저 돌리세요"** 로 끝난다 —
   신호는 멀쩡히 있는데도. 그리고 두 나라가 같은 날 돌면 **우연히 통과한다.**
   틀린 줄 모르고 지나가다 어느 날만 안 되는, 가장 찾기 나쁜 고장이다.

이 파일은 세 번째를 막는다. SQL 을 실제로 돌려 보지는 못하지만(그러려면 두 나라의 데이터가
필요하다), **"나라별 표의 최신 날짜를 나라 조건 없이 잡는 문장"** 은 글자로 찾을 수 있다.

나라별로 갈리지 않는 표는 `전체가_한_번에` 에 이유와 함께 적는다. 적는 순간 그 표가
나라별로 갈리게 바뀌면 이 목록을 다시 봐야 한다는 뜻이 된다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent

#: 나라마다 따로 계산돼 **날짜가 어긋날 수 있는** 표. 최신 날짜를 나라 조건 없이 잡으면 안 된다
나라별_표 = {
    "signals": "signals.run(market) 이 나라마다 따로 돈다",
    "scores": "scores.run(market) 이 나라마다 따로 돈다",
    "universe_members": "유니버스 갱신이 나라마다 다른 날 돈다 (2026-09-17)",
    "signal_checks": "신호 배치가 남기므로 신호와 같은 기준일이다",
    # 2026-09-26 까지 "metrics.run(countries=('KR','US')) 이 한 번에 돌며 같은 as_of 를 쓴다" 는 사유로 면제였다.
    # 틀렸다 — d1-catchup.yml·metrics.yml 이 `--market KR` 로 국내만 돌린다 (docs/infra.md 25.248)
    "performance_metrics": "metrics.run 이 `--market` 으로 나라 하나만 돌 수 있다 (d1-catchup.yml)",
}

#: 한 번에 두 나라를 함께 쓰는 표. 전체 MAX 로 잡아도 한 나라가 사라지지 않는다.
#: **이유를 적어야 들어올 수 있다.** 그 전제가 깨지면 여기부터 다시 본다
전체가_한_번에 = {
    "sell_flags": "sell_flags.run() 이 load_holdings() 로 **두 나라 보유를 한꺼번에** 읽어"
    " 같은 as_of_date 로 쓴다. 나라별로 쪼개는 순간 이 전제가 깨진다",
    "financial_snapshots": "국내(DART)만 있다. 나라가 하나뿐이라 갈릴 것이 없다",
    "sentiment_scores": "종목별로 잡는다(WHERE x.stock_id = ...). 나라 전체 MAX 가 아니다",
}

#: 최신 날짜를 담는 열 이름
날짜열 = ("as_of_date", "snapshot_date")

#: 서브쿼리를 이만큼 좁히면 나라가 섞일 일이 없다.
#: `stock_id` 는 나라보다 좁다 — 한 종목은 한 나라에만 있다
좁히는_말 = ("country", "stock_id")

#: 걸렸지만 괜찮은 자리. **이유를 적어야 들어올 수 있다**
봐주는_곳 = {
    ("batch/core/db.py", "universe_members"): "SQL 이 아니라 주석이다 —"
    " 예전에 이렇게 써서 미국 종목이 사라졌던 일을 설명하는 문장이다",
}

#: 훑을 곳. 웹은 SQL 을 담는 lib 과 API 경로
찾는_곳 = ("batch", "scripts", "web/lib", "web/app")
확장자 = ("*.py", "*.ts")

#: `MAX(...)` 안의 날짜 열과, 그 뒤 같은 문장에서 표 이름을 찾는다
패턴 = re.compile(
    r"MAX\(\s*(?:\w+\.)?(" + "|".join(날짜열) + r")\s*\)(?P<뒤>.{0,400})",
    re.S,
)


def 파일들() -> list[Path]:
    out: list[Path] = []
    for 디렉터리 in 찾는_곳:
        for 패턴자 in 확장자:
            out += [
                p
                for p in (뿌리 / 디렉터리).rglob(패턴자)
                if "__pycache__" not in p.parts and "node_modules" not in p.parts
            ]
    return sorted(out)


def 걸린것들() -> list[tuple[Path, int, str, str]]:
    """(파일, 줄, 표 이름, 문장 조각). 나라별 표를 최신 날짜로 잡는 자리들."""
    결과 = []
    for 경로 in 파일들():
        본문 = 경로.read_text(encoding="utf-8")
        for m in 패턴.finditer(본문):
            뒤 = m.group("뒤")
            표 = re.search(r"FROM\s+(\w+)", 뒤)
            if not 표:
                continue
            이름 = 표.group(1)
            if 이름 not in 나라별_표:
                continue
            # 그 서브쿼리가 끝나기 전에 country 조건이 있는가.
            # 닫는 괄호까지만 본다 — 바깥 질의의 country 는 서브쿼리를 좁히지 못한다
            조각 = 뒤[: 뒤.find(")", 뒤.find(이름))] if ")" in 뒤 else 뒤
            if any(말 in 조각 for 말 in 좁히는_말):
                continue
            열쇠 = (str(경로.relative_to(뿌리)), 이름)
            if 열쇠 in 봐주는_곳:
                continue
            결과.append((경로, 본문[: m.start()].count("\n") + 1, 이름, 조각.strip()[:120]))
    return 결과


def test_훑을_파일이_있다() -> None:
    """훑기가 조용히 0개를 내면 아래가 무조건 통과한다."""
    목록 = 파일들()

    assert len(목록) > 100, f"{len(목록)}개만 훑었다"
    assert any(p.name == "stress.py" for p in 목록)


def test_패턴이_실제로_잡는다() -> None:
    """거르개가 망가져 아무것도 안 잡으면 이 파일 전체가 장식이 된다.

    2026-09-21 에 고치기 전의 `stress.py` 문장을 그대로 넣어 본다.
    """
    나쁜예 = (
        "SELECT sg.stock_id FROM signals sg JOIN stocks s ON s.id = sg.stock_id"
        " WHERE s.country = ? AND sg.as_of_date = (SELECT MAX(as_of_date) FROM signals)"
    )
    좋은예 = (
        "sg.as_of_date = (SELECT MAX(sg2.as_of_date) FROM signals sg2"
        " JOIN stocks s2 ON s2.id = sg2.stock_id WHERE s2.country = ?)"
    )

    def 걸리나(sql: str) -> bool:
        m = 패턴.search(sql)
        if not m:
            return False
        뒤 = m.group("뒤")
        표 = re.search(r"FROM\s+(\w+)", 뒤)
        if not 표 or 표.group(1) not in 나라별_표:
            return False
        조각 = 뒤[: 뒤.find(")", 뒤.find(표.group(1)))] if ")" in 뒤 else 뒤
        return not any(말 in 조각 for 말 in 좁히는_말)

    assert 걸리나(나쁜예), "고치기 전의 문장을 못 잡는다 — 거르개가 망가졌다"
    assert not 걸리나(좋은예), "고친 문장까지 잡는다 — 거짓 경보가 난다"


def test_나라별_표를_나라_구분_없이_잡는_곳이_없다() -> None:
    걸림 = 걸린것들()

    보고 = "\n".join(
        f"  {p.relative_to(뿌리)}:{줄}  [{표}]  {조각}" for p, 줄, 표, 조각 in 걸림
    )
    assert not 걸림, (
        "나라별로 날짜가 갈리는 표의 최신 날짜를 나라 조건 없이 잡는다."
        " 늦게 돈 나라의 날짜가 잡히면 다른 나라가 통째로 사라진다"
        f" (docs/infra.md 25.44):\n{보고}"
    )


def test_종목별로_좁힌_것은_걸리지_않는다() -> None:
    """`WHERE stock_id = ?` 는 나라보다 좁다. 이것까지 잡으면 거짓 경보가 쏟아진다."""
    m = 패턴.search("sc.as_of_date = (SELECT MAX(as_of_date) FROM scores WHERE stock_id = s.id)")

    assert m and "stock_id" in m.group("뒤")


@pytest.mark.parametrize("열쇠", sorted(봐주는_곳))
def test_봐주는_곳은_이유가_적혀_있다(열쇠: tuple[str, str]) -> None:
    assert 봐주는_곳[열쇠].strip(), f"{열쇠} 의 이유가 비어 있다"
    assert (뿌리 / 열쇠[0]).exists(), f"{열쇠[0]} 이 없다 — 목록이 낡았다"


def test_상태_화면이_나라별로_보여_준다() -> None:
    """2026-09-21 에 고친 자리. 전체 MAX 한 줄이면 **한 나라가 멈춘 것을 가린다.**

    지금 미국은 통째로 쉬고 있다(25.14). 그 화면이 "점수 09-21" 한 줄만 보여 주면
    국내만 보고 싱싱하다고 말하는 셈이다.
    """
    본문 = (뿌리 / "web" / "lib" / "health.ts").read_text(encoding="utf-8")

    for 열쇠 in ("scores_kr", "scores_us", "signals_kr", "signals_us", "universe_kr", "universe_us"):
        assert f'key: "{열쇠}"' in 본문, f"상태 화면에 {열쇠} 가 없다"


@pytest.mark.parametrize("표이름", sorted(전체가_한_번에))
def test_전체_MAX_로_잡아도_되는_표는_이유가_적혀_있다(표이름: str) -> None:
    assert 전체가_한_번에[표이름].strip(), f"{표이름} 의 이유가 비어 있다"
    assert 표이름 not in 나라별_표, f"{표이름} 이 양쪽에 다 있다"


def test_stress_가_나라별로_잡는다() -> None:
    """2026-09-21 에 고친 자리. 되돌아가면 한 나라의 스트레스 배치가 늘 실패한다."""
    본문 = (뿌리 / "batch" / "jobs" / "stress.py").read_text(encoding="utf-8")

    # 25.359 부터는 아침 리포트와 같은 "마지막 계산일" 함수를 쓴다 — 그 함수가 나라별로 잡는다
    assert "db.last_signal_calc_date(client, country" in 본문
    함수 = (뿌리 / "batch" / "core" / "db.py").read_text(encoding="utf-8")
    assert "WHERE s.country = ? AND sg.as_of_date <= ? UNION ALL" in 함수
