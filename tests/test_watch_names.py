"""무응답 감시가 **있는 이름**을 지켜보는지 (docs/infra.md 25.77).

`web/lib/health.ts` 의 `WATCHES` 는 "이 작업이 제때 성공했는가" 를 보는 목록이고,
그 판단은 `batch_runs.job_name` 을 **글자 그대로** 맞춰 본다.

```ts
const last = findLastSuccess(lastSuccess, watch);   // 이름 **과 시장**이 맞아야 한다 (25.128)
if (countsForToday(last, session)) continue;        // 오늘 것은 이미 성공했다
```

**그 글자가 배치가 쓰는 이름과 다르면** 성공 기록을 영영 못 찾는다. 그러면 배치가 잘 돌아도
`last` 가 null 이라 **매일 "무응답" 이 울린다.** 거짓 경보가 쌓이면 사람은 곧 무시하게 되고,
진짜로 멈춘 날에도 무시한다. **감시가 스스로를 무력하게 만드는 길이다.**

이름은 두 언어에 따로 적혀 있다 — 배치는 `JOB_NAME` 상수(또는 `daily.job_name()`),
웹은 `WATCHES[].job`. 묶어 두지 않으면 한쪽만 고쳐질 때 갈라진다(docs/infra.md 25.0
"한 규칙이 두 곳에 있다").
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
HEALTH = 뿌리 / "web" / "lib" / "health.ts"


def 감시목록() -> list[dict[str, str]]:
    """`WATCHES` 의 각 줄에서 job·market·label."""
    글 = HEALTH.read_text(encoding="utf-8")
    몸통 = 글.split("export const WATCHES: Watch[] = [", 1)[1].split("\n];", 1)[0]
    나온것 = []
    for 줄 in 몸통.splitlines():
        m = re.search(r'job:\s*"([^"]+)".*?market:\s*"([^"]+)".*?label:\s*"([^"]+)"', 줄)
        if m:
            나온것.append({"job": m.group(1), "market": m.group(2), "label": m.group(3)})
    return 나온것


def 배치이름들() -> set[str]:
    """배치가 `batch_runs.job_name` 에 실제로 넣는 이름."""
    이름 = set()
    for 길 in sorted((뿌리 / "batch" / "jobs").glob("*.py")):
        for 마디 in ast.walk(ast.parse(길.read_text(encoding="utf-8"))):
            if (
                isinstance(마디, ast.Assign)
                and any(getattr(t, "id", "") == "JOB_NAME" for t in 마디.targets)
                and isinstance(마디.value, ast.Constant)
            ):
                이름.add(str(마디.value.value))
    # daily 는 시장마다 이름을 만든다 (`daily.job_name`)
    from batch.jobs import daily

    이름 |= {daily.job_name("KR"), daily.job_name("US")}
    return 이름


감시 = 감시목록()
배치 = 배치이름들()


def test_읽어_냈다() -> None:
    """0개면 아래가 공짜로 통과한다."""
    assert len(감시) >= 3, f"WATCHES 를 {len(감시)}개밖에 못 읽었다 — 모양이 바뀌었나"
    assert len(배치) >= 20, f"배치 이름을 {len(배치)}개밖에 못 읽었다"


@pytest.mark.parametrize("watch", 감시, ids=[w["job"] for w in 감시])
def test_감시하는_이름이_실제로_있다(watch: dict[str, str]) -> None:
    assert watch["job"] in 배치, (
        f"`{watch['job']}`({watch['label']}) 라는 이름으로 도는 배치가 없다.\n"
        "성공 기록을 못 찾으면 배치가 잘 돌아도 **매일 무응답이 울린다**.\n"
        f"배치가 쓰는 이름: {sorted(배치)}"
    )


def test_국내_일일_배치를_지켜본다() -> None:
    """가장 중요한 하나. 이것이 빠지면 아침 리포트가 안 와도 아무도 모른다."""
    from batch.jobs import daily

    assert daily.job_name("KR") in {w["job"] for w in 감시}


def test_시장_표시가_배치와_맞는다() -> None:
    """`daily_kr` 를 US 로 지켜보면 세션을 엉뚱한 달력에서 찾는다."""
    from batch.jobs import daily

    시장 = {w["job"]: w["market"] for w in 감시}

    assert 시장.get(daily.job_name("KR")) == "KR"
    assert 시장.get(daily.job_name("US")) == "US"


def test_지켜보지_않는_배치가_무엇인지_세어_둔다() -> None:
    """**빠뜨림과 판단을 가른다** (25.76 과 같은 이유).

    28개 중 셋만 지켜본다. 나머지가 안 돌아도 무응답 알림은 없다 — 그것은 판단이다.
    사람이 매일 여는 화면(아침 리포트·추천)에 곧바로 드러나는 것만 지켜보고,
    주 1회·월 1회짜리는 `/status` 의 "데이터 신선도" 로 본다(`FRESHNESS`).
    이 수가 크게 흔들리면 그 판단을 다시 해야 한다.
    """
    안봄 = 배치 - {w["job"] for w in 감시}

    # 31: 국내 수급 수집(kis_flows, 25.987)은 `FRESHNESS` 의 "국내 수급" 칸이 본다
    # 32: 증권사 성적표(broker_stats, 25.995)는 수급 수집 끝에 주 1회 — 종목 화면에 기준일이 보인다
    # 33: 공시 반응 통계(disclosure_reaction, 25.996)도 수급 수집 끝에 — 공시 알림의 줄에 기준일이 없어도 표가 비면
    # 줄이 안 붙을 뿐이다
    assert len(안봄) <= 33, f"배치가 너무 늘었다. 무응답 감시 목록을 다시 보라: {sorted(안봄)}"
    assert len(감시) <= 8, "감시가 늘었다면 좋은 일이다 — 이 문턱과 위 설명을 함께 고쳐라"


def _시장을_인자로_받는_작업() -> set[str]:
    """`start_batch_run(market=<변수>)` 처럼 **한 이름으로 여러 시장에서 도는** 작업.

    `sentiment` 가 그렇다 — `daily_kr`·`daily_us` 가 각각 `sentiment.run(market)` 을 부르고
    둘 다 `batch_runs(job_name='sentiment')` 를 남긴다.
    """
    나온것: set[str] = set()
    for 길 in sorted((뿌리 / "batch" / "jobs").glob("*.py")):
        나무 = ast.parse(길.read_text(encoding="utf-8"))
        이름 = next(
            (
                str(마디.value.value)
                for 마디 in ast.walk(나무)
                if isinstance(마디, ast.Assign)
                and any(getattr(t, "id", "") == "JOB_NAME" for t in 마디.targets)
                and isinstance(마디.value, ast.Constant)
            ),
            None,
        )
        if 이름 is None:
            continue
        for 마디 in ast.walk(나무):
            if not (isinstance(마디, ast.Call) and isinstance(마디.func, ast.Attribute)):
                continue
            if 마디.func.attr != "start_batch_run":
                continue
            for kw in 마디.keywords:
                # market="KR" 처럼 **글자로 박힌** 것은 한 시장짜리다. 변수면 여럿이다
                if kw.arg == "market" and not isinstance(kw.value, ast.Constant):
                    나온것.add(이름)
    return 나온것


def test_한_이름으로_두_시장에서_도는_작업은_시장까지_맞춰_본다() -> None:
    """**감시가 제 눈을 가리지 않게 한다** (docs/infra.md 25.128).

    `sentiment` 처럼 한 이름이 두 시장에서 돌면, 시장을 안 보는 조회는 **미국의 성공으로
    국내의 침묵을 덮는다.** 국내 감성이 몇 주 죽어 있어도 조용하다.

    고친 뒤라 지금은 통과한다. 여기서 막는 것은 **되돌아가는 것**이다 — 이 성질을 가진
    작업이 감시 목록에 있는 한, 웹 쪽 조회는 시장까지 맞춰야 한다.
    """
    글 = HEALTH.read_text(encoding="utf-8")
    여럿 = _시장을_인자로_받는_작업()
    assert 여럿, "시장을 인자로 받는 작업을 하나도 못 찾았다 — 표기가 바뀌었나"

    걸린것 = sorted(여럿 & {w["job"] for w in 감시})
    if not 걸린것:
        return  # 그런 작업을 지켜보지 않으면 이 위험은 없다

    assert "GROUP BY job_name, market" in 글, (
        f"{걸린것} 은(는) 한 이름으로 여러 시장에서 돈다. "
        "LAST_SUCCESS 가 job_name 으로만 묶으면 한쪽 시장의 성공이 다른 쪽을 덮는다"
    )
    assert "findLastSuccess" in 글, (
        f"{걸린것} 의 성공을 이름으로만 찾으면 시장이 섞인다 (docs/infra.md 25.128)"
    )


def test_집계만_한_국내_감성은_채점_감시를_채우지_않는다() -> None:
    """daily_kr 의 집계 전용 실행이 '국내 뉴스 감성 채점' 감시를 채워 채점이 멈춰도 조용했다 (docs/infra.md 25.371)."""
    본문 = (뿌리 / "batch" / "jobs" / "sentiment.py").read_text(encoding="utf-8")
    assert 'AGGREGATE_JOB_NAME = "sentiment_aggregate"' in 본문
    assert "job_name=JOB_NAME if score else AGGREGATE_JOB_NAME" in 본문
    감시 = (뿌리 / "web" / "lib" / "health.ts").read_text(encoding="utf-8")
    assert '{ job: "sentiment", market: "KR", label: "국내 뉴스 감성 채점"' in 감시
