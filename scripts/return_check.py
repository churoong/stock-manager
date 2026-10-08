"""되살아나는 날 확인할 것을 **실행이 스스로 판정한다** (docs/infra.md 25.80).

**왜 있나.** 2026-09-20~21 에 고친 스물몇 가지는 **하나도 돌려 보지 못했다** — Actions 가
무료 몫 소진으로 멈춰 있었다(25.69). 되살아나는 날(10월 1일 예상)은 분이 곧 돈이라
**따라잡기 한 번**으로 최대한 많이 확인해야 한다. 그런데 인계 메모의 확인 목록은
**사람이 로그 일곱 줄을 읽고 해석하는** 것이었다.

그 방식의 문제는 세 가지다.

1. **한 번 도는 실행에서 못 본 것은 다음 날까지 모른다.** 읽다 빠뜨리면 하루를 잃는다.
2. **클라우드 세션은 로그 원문을 못 받는다**(25.17). 사람이 옮겨 적어 줘야 한다.
3. **해석이 곧 판단이다.** "편입 0" 이 고장인지 "지난 스냅샷을 지켰다" 인지(25.64),
   "베타 없음" 이 지수 부족인지 배선 오류인지(25.65) — 그 갈림은 코드가 안다.

그래서 여기서 판정까지 해서 `catchup.log` 에 찍는다. 로그는 이슈로 올라가므로(25.21)
세션이 그대로 읽는다. 사람이 할 일은 **✗ 가 있나 보는 것**뿐이다.

**읽기만 한다. 무슨 일이 있어도 0 으로 끝난다.** 확인하는 일이 확인받는 일을 망치면 안 된다
(scripts/d1_usage.py 와 같은 약속).

**모르는 것을 ✓ 로 적지 않는다.** 못 읽었으면 `?` 다 — 이것이 25.74·d1_usage 에서 되풀이된
모양이다("못 읽음" 과 "없음" 을 한 글자로 적으면 사람이 엉뚱한 판단을 한다).

실행
  python scripts/return_check.py                 # 워크플로가 STEPS_JSON·GITHUB_EVENT_NAME 을 넘긴다
  python scripts/return_check.py --usage-log d1-usage.log
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core import db  # noqa: E402

# 지수 코드와 최소 길이를 **여기서 다시 적지 않는다.** 25.65 가 바로 "한 규칙이 두 곳에 있다"
# 였고(코드를 'S&P500' 으로 잘못 적어 두었다), 확인하는 쪽이 같은 실수를 하면 확인이 무의미하다
from batch.jobs.metrics import BENCHMARK, MIN_BENCHMARK_POINTS  # noqa: E402

#: 4단계(시세)가 죽어도 **돌아 있어야 하는** 단계들 (docs/infra.md 25.63).
#: 워크플로 step id → 사람이 읽는 이름. d1-catchup.yml 의 5~8번이다
뒷단계 = {
    "metrics": "5. 성과 지표",
    "scores": "6. 점수",
    "signals": "7. 신호",
    "targets": "8. 장중 감시",
}
#: 4단계가 뒤에 남겨 두는 여유. d1-catchup.yml 의 `--budget-reserve` 와 같아야 한다.
#: **어림이다**(infra 25.8·25.26) — 이 스크립트가 실측으로 바꾸려고 있는 숫자다
예산여유 = 32_000
#: 성과 지표가 베타를 내려면 지수가 이만큼 있어야 한다. **metrics.py 에서 가져온다**
베타_최소_지수일 = MIN_BENCHMARK_POINTS


@dataclass
class 판정:
    """한 줄의 확인. `상태` 는 `ok` · `bad` · `unknown` 셋뿐이다."""

    이름: str
    상태: str
    본것: str
    다음: str = ""

    def 줄(self) -> str:
        표 = {"ok": "✓", "bad": "✗", "unknown": "?"}[self.상태]
        글 = f"[복귀 점검] {표} {self.이름}: {self.본것}"
        return f"{글}\n              → {self.다음}" if self.다음 else 글


# ---------------------------------------------------------------- 순수 판정들
# 전부 인자만 보고 판단한다. DB 도 환경도 안 본다 — 테스트가 이것을 본다


def 뒷단계가_돌았나(steps_json: str | None) -> 판정:
    """25.63: 4단계가 죽어도 5~8단계가 돌아야 한다.

    `if: always() && ...` 로 고쳤지만 **운영에서 확인한 적이 없다.** 안 먹었으면 `outcome` 이
    `skipped` 로 남는다.
    """
    이름 = "5~8단계가 4단계와 무관하게 돌았나 (25.63)"
    try:
        steps = json.loads(steps_json or "")
    except ValueError:
        steps = None
    if not isinstance(steps, dict) or not steps:
        return 판정(이름, "unknown", "STEPS_JSON 을 못 읽었다", "워크플로가 toJSON(steps) 를 넘기는지 본다")

    건너뜀 = [
        글 for 키, 글 in 뒷단계.items() if isinstance(steps.get(키), dict) and steps[키].get("outcome") == "skipped"
    ]
    안보임 = [글 for 키, 글 in 뒷단계.items() if not isinstance(steps.get(키), dict)]
    if 안보임:
        return 판정(이름, "unknown", f"단계가 안 보인다: {', '.join(안보임)}", "step id 가 바뀌었는지 본다")
    if not 건너뜀:
        # 읽기 문이 미룬 단계도 outcome 은 success 다 — "돌았다" 와 가른다 (25.847)
        def 결과(키: str) -> str:
            미룸 = (steps[키].get("outputs") or {}).get("deferred") == "true"
            return "미룸(읽기 문)" if 미룸 else steps[키]["outcome"]

        돈것 = ", ".join(f"{글} {결과(키)}" for 키, 글 in 뒷단계.items())
        return 판정(이름, "ok", f"넷 다 돌았다 ({돈것})")

    앞 = steps.get("backfill", {})
    앞결과 = 앞.get("outcome", "?") if isinstance(앞, dict) else "?"
    return 판정(
        이름,
        "bad",
        f"건너뛰었다: {', '.join(건너뜀)} (4단계는 {앞결과})",
        "`if: always() && steps.check.outputs.use_d1` 이 안 먹었다. d1-catchup.yml 조건을 다시 본다",
    )


def 사용량_읽기(글: str) -> list[tuple[str, int]]:
    """`d1-usage.log` 에서 (라벨, 그때까지 **쓴** 행). 못 읽은 줄은 버린다.

    `scripts/d1_usage.py` 가 찍는 모양:
    `[D1 사용량] 4. 과거 시세 끝: 오늘 72,340행 썼다 (72%), 남은 …`

    **머리표를 여기서 다시 적지 않는다** (25.113). 같은 파일이 읽은 행도
    `[D1 읽기량]` 으로 찍는데, 머리표를 손으로 베껴 두면 한쪽이 바뀔 때
    **읽은 행(수백만)을 쓴 행으로 읽고** 여유 판정이 통째로 뒤집힌다.
    """
    try:  # 워크플로는 `python scripts/return_check.py` 로 부른다(그 디렉터리가 sys.path[0])
        from d1_usage import WRITE_MARK
    except ImportError:  # 저장소 뿌리에서 패키지로 부를 때
        from scripts.d1_usage import WRITE_MARK

    나온것 = []
    for 줄 in 글.splitlines():
        m = re.search(re.escape(WRITE_MARK) + r"\s*(.+?):\s*오늘\s*([\d,]+)행", 줄)
        if m:
            나온것.append((m.group(1).strip(), int(m.group(2).replace(",", ""))))
    return 나온것


def 여유가_맞았나(사용량: list[tuple[str, int]]) -> 판정:
    """25.26: `--budget-reserve 32000` 의 **한 조각을 어림에서 실측으로** 바꾼다.

    4단계가 끝난 뒤부터 마지막까지 늘어난 행이 곧 5~8단계가 쓴 몫이다.

    **여기서 여유를 줄이라고 말하지 않는다.** 여유 32,000 은 세 몫의 합이기 때문이다
    (d1-catchup.yml 4단계 주석): 5~8단계 + 웹 크론 + **다음 날 08:27 국내 일일 배치.**
    5~8단계만 재고 "여유를 줄여도 되겠다" 고 판단하면 **다음 날 아침 리포트를 굶긴다** —
    바로 2026-09-19·20 에 재무·업종이 이틀 내리 굶었던 그 모양이다(25.20).
    넘치는 것은 확실히 알 수 있지만, 모자라지 않는다는 것은 **세 몫을 다 재야** 안다.
    """
    이름 = f"--budget-reserve {예산여유:,} 중 5~8단계 몫 (25.26)"
    시세끝 = next((수 for 라벨, 수 in 사용량 if 라벨.startswith("4.")), None)
    if 시세끝 is None or len(사용량) < 2:
        모름 = "d1-usage.log 가 비었는지, `|| rc=$?` 가 빠졌는지 본다"
        return 판정(이름, "unknown", "단계별 사용량을 못 읽었다", 모름)

    끝 = 사용량[-1][1]
    뒤가쓴 = 끝 - 시세끝
    if 뒤가쓴 < 0:
        return 판정(이름, "unknown", f"카운터가 줄었다 ({시세끝:,} → {끝:,})", "UTC 자정을 넘겨 카운터가 리셋됐다")

    몫 = f"5~8단계가 {뒤가쓴:,}행 썼다 (여유 {예산여유:,})"
    if 뒤가쓴 > 예산여유:
        return 판정(
            이름, "bad", 몫,
            f"이 한 몫만으로 여유를 넘었다. {뒤가쓴 + 5_000:,} 이상으로 늘린다 (d1-catchup.yml 4단계)",
        )  # fmt: skip
    return 판정(
        이름, "ok", 몫,
        f"남은 {예산여유 - 뒤가쓴:,}행이 웹 크론 + 다음 날 08:27 일일 배치 몫이다."
        " 그 둘을 재기 전에는 여유를 줄이지 않는다 — 줄이면 아침 리포트가 굶는다",
    )  # fmt: skip


def 단계별_사용량이_다_찍혔나(사용량: list[tuple[str, int]]) -> 판정:
    """25.52: 단계가 죽어도 사용량은 재고 넘어가야 한다 (`|| rc=$?`).

    죽은 단계의 숫자를 잃으면 **예산을 가장 알고 싶은 순간에** 그 숫자가 없다.
    """
    이름 = "단계별 사용량이 다 남았나 (25.52)"
    번호 = {라벨[0] for 라벨, _ in 사용량 if 라벨[:1].isdigit()}
    빠짐 = sorted({"1", "2", "3", "4", "5", "6", "7", "8"} - 번호)
    if not 사용량:
        return 판정(이름, "unknown", "사용량 줄이 하나도 없다", "d1-usage.log 를 넘겼는지 본다")
    if 빠짐:
        return 판정(
            이름, "bad",
            f"{len(번호)}/8 단계만 찍혔다 (빠진 단계: {', '.join(빠짐)})",
            "그 단계의 `|| rc=$?` 가 빠졌다",
        )  # fmt: skip
    return 판정(이름, "ok", "8단계 전부 찍혔다")


def 무엇이_깨웠나(event: str | None) -> 판정:
    """`repository_dispatch` 면 cron-job.org 가 등록돼 있다 → docs/todo-user.md 1번 ✅."""
    이름 = "무엇이 깨웠나 (todo-user 1)"
    if not event:
        return 판정(이름, "unknown", "GITHUB_EVENT_NAME 이 비었다")
    if event == "repository_dispatch":
        return 판정(이름, "ok", "cron-job.org 가 깨웠다 — todo-user 1번을 지운다")
    if event == "workflow_dispatch":
        return 판정(이름, "unknown", "사람이 손으로 깨웠다 — 등록 여부는 이걸로 알 수 없다")
    return 판정(이름, "bad", f"{event} 가 깨웠다", "cron-job.org 등록 전이다 (docs/todo-user.md 1번)")


def 유니버스_판정(편입: int | None, 시세거래일: int | None) -> 판정:
    """25.64: 편입 0 은 **고장일 수도, 지켜 낸 것일 수도** 있다. 그 갈림을 여기서 짓는다."""
    이름 = "유니버스 편입 (25.64)"
    if 편입 is None:
        return 판정(이름, "unknown", "유니버스를 못 읽었다")
    if 편입 > 0:
        return 판정(이름, "ok", f"편입 {편입:,}종목")
    if 시세거래일 is not None and 시세거래일 < 20:
        return 판정(
            이름, "ok",
            f"편입 0 인데 시세가 {시세거래일}거래일뿐이다",
            "고장이 아니다 — 20거래일 평균 거래대금을 못 재서 **지난 스냅샷을 지킨 것**이다",
        )  # fmt: skip
    return 판정(이름, "bad", f"편입 0 인데 시세는 {시세거래일}거래일 있다", "1단계 로그에서 제외 사유를 본다")


def 베타_판정(베타있음: int | None, 성과지표: int | None, 지수일: int | None) -> 판정:
    """25.65: 베타는 **한 번도 계산된 적이 없었다** (`market=` 하나가 안 넘어갔다)."""
    이름 = "베타가 채워졌나 (25.65)"
    if 베타있음 is None or 성과지표 is None:
        return 판정(이름, "unknown", "성과 지표를 못 읽었다")
    if 성과지표 == 0:
        return 판정(이름, "unknown", "성과 지표가 아직 0행이다", "5단계가 굶었는지 본다")
    if 베타있음 > 0:
        return 판정(이름, "ok", f"{베타있음:,}/{성과지표:,}행에 베타가 있다")
    if 지수일 is not None and 지수일 < 베타_최소_지수일:
        return 판정(
            이름, "bad",
            f"베타가 0행이다 — 지수가 {지수일}일뿐이다 (최소 {베타_최소_지수일})",
            "`python -m batch.jobs.index_prices --market KR --lookback 2000` 을 한 번 돌린다",
        )  # fmt: skip
    return 판정(
        이름, "bad",
        f"지수는 {지수일}일 있는데 베타가 0행이다",
        "batch/jobs/metrics.py 가 market= 를 넘기는지 본다",
    )  # fmt: skip


def 센티먼트_판정(점수행: int | None, 센티먼트있음: int | None, 감성행: int | None) -> 판정:
    """25.66: 총점에는 섞이는데 **적힌 적은 없었다** — `sentiment_at_trade` 가 영구 NULL 이었다."""
    이름 = "센티먼트가 따로 적히나 (25.66)"
    if 점수행 is None or 센티먼트있음 is None:
        return 판정(이름, "unknown", "점수를 못 읽었다")
    if 점수행 == 0:
        return 판정(이름, "unknown", "점수가 아직 0행이다", "6단계가 굶었는지 본다")
    if 센티먼트있음 > 0:
        return 판정(이름, "ok", f"{센티먼트있음:,}/{점수행:,}행에 센티먼트가 적혔다")
    if not 감성행:
        return 판정(이름, "ok", "센티먼트가 비었지만 감성 점수 자체가 0행이다", "뉴스가 쌓인 뒤에 다시 본다")
    return 판정(
        이름, "bad",
        f"감성은 {감성행:,}행 있는데 점수에 센티먼트가 0행이다",
        "batch/jobs/scores.py 가 total.sentiment_used 를 넣는지 본다",
    )  # fmt: skip


# ---------------------------------------------------------------- DB 에서 읽기


def _수(client: Any, sql: str, args: list[Any] | None = None) -> int | None:
    """못 읽으면 **0 이 아니라 None** 이다 (d1_usage.py 와 같은 약속)."""
    try:
        rows = client.execute(sql, args or []).rows
    except Exception:  # noqa: BLE001 — 확인하는 일이 확인받는 일을 망치면 안 된다
        return None
    return int(rows[0][0]) if rows and rows[0][0] is not None else 0


@dataclass
class 사실:
    """DB 에서 읽은 것. 못 읽은 것은 None 으로 둔다 — 지어내지 않는다."""

    편입: int | None = None
    시세거래일: int | None = None
    성과지표: int | None = None
    베타있음: int | None = None
    지수일: int | None = None
    점수행: int | None = None
    센티먼트있음: int | None = None
    감성행: int | None = None


def 모으기(client: Any, country: str = "KR") -> 사실:
    """**질의를 한 덩이씩 그대로 적는다.** 공통 조각을 `+` 로 이어 붙이면 짧아지지만,
    `tests/test_sql_schema.py` 의 그물이 못 읽는 "조각" 이 된다 — 스키마와 어긋나도 모른다.
    실제로 이 함수를 조각으로 쓰자 그 테스트가 곧바로 잡았다 (2026-09-21, 25.61 이 일한 것).
    """
    return 사실(
        편입=_수(
            client,
            "SELECT COUNT(*) FROM universe_members u JOIN stocks s ON s.id = u.stock_id"
            f" WHERE u.included = 1 AND s.country = ? AND u.snapshot_date = {db.latest_snapshot_sql()}",
            [country, country],
        ),
        # 날짜 색인을 한 칸씩 건너뛰며 센다 (docs/infra.md 25.1034, `catchup_report` 25.866 과 같은 꼴) —
        # `COUNT(DISTINCT p.date)` 는 그 나라 시세 행을 전부(국내 약 수백만 행) 읽었다
        시세거래일=_수(
            client,
            "WITH RECURSIVE d(x) AS (SELECT (SELECT MIN(date) FROM prices)"
            " UNION ALL SELECT (SELECT MIN(date) FROM prices WHERE date > d.x) FROM d WHERE d.x IS NOT NULL)"
            " SELECT COUNT(*) FROM d WHERE x IS NOT NULL AND EXISTS (SELECT 1 FROM prices p"
            "   CROSS JOIN stocks s WHERE p.date = d.x AND s.id = p.stock_id AND s.country = ?)",
            [country],
        ),
        성과지표=_수(
            client,
            "SELECT COUNT(*) FROM performance_metrics pm JOIN stocks s ON s.id = pm.stock_id WHERE s.country = ?",
            [country],
        ),
        베타있음=_수(
            client,
            "SELECT COUNT(*) FROM performance_metrics pm JOIN stocks s ON s.id = pm.stock_id"
            " WHERE s.country = ? AND pm.beta IS NOT NULL",
            [country],
        ),
        지수일=_수(client, "SELECT COUNT(*) FROM index_prices WHERE index_code = ?", [BENCHMARK[country]]),
        점수행=_수(
            client,
            "SELECT COUNT(*) FROM scores sc JOIN stocks s ON s.id = sc.stock_id WHERE s.country = ?",
            [country],
        ),
        센티먼트있음=_수(
            client,
            "SELECT COUNT(*) FROM scores sc JOIN stocks s ON s.id = sc.stock_id"
            " WHERE s.country = ? AND sc.sentiment_score IS NOT NULL",
            [country],
        ),
        감성행=_수(
            client,
            "SELECT COUNT(*) FROM sentiment_scores ss JOIN stocks s ON s.id = ss.stock_id WHERE s.country = ?",
            [country],
        ),
    )


def 전부(steps_json: str | None, event: str | None, 사용량글: str, f: 사실) -> list[판정]:
    """순서는 docs/infra.md 25.80 의 표와 같다."""
    사용량 = 사용량_읽기(사용량글)
    return [
        뒷단계가_돌았나(steps_json),
        단계별_사용량이_다_찍혔나(사용량),
        여유가_맞았나(사용량),
        유니버스_판정(f.편입, f.시세거래일),
        베타_판정(f.베타있음, f.성과지표, f.지수일),
        센티먼트_판정(f.점수행, f.센티먼트있음, f.감성행),
        무엇이_깨웠나(event),
    ]


def 머리말(판정들: list[판정]) -> str:
    나쁨 = sum(1 for p in 판정들 if p.상태 == "bad")
    모름 = sum(1 for p in 판정들 if p.상태 == "unknown")
    꼬리 = f"고칠 것 {나쁨}건" if 나쁨 else ("고칠 것 없음" if not 모름 else "고칠 것 없음")
    if 모름:
        꼬리 += f" · 못 본 것 {모름}건"
    return f"[복귀 점검] === {len(판정들)}가지 확인 — {꼬리} (docs/infra.md 25.80) ==="


#: d1-catchup 0단계가 판정한 DB (`steps.check.outputs.backend`). 워크플로가 이 이름으로 넘긴다
CATCHUP_BACKEND_ENV = "CATCHUP_BACKEND"


def main() -> int:
    parser = argparse.ArgumentParser(description="되살아나는 날 확인 (docs/infra.md 25.80)")
    parser.add_argument("--usage-log", default="d1-usage.log", help="scripts/d1_usage.py 가 쌓은 로그")
    parser.add_argument("--market", default="KR")
    args = parser.parse_args()

    try:
        사용량글 = Path(args.usage_log).read_text(encoding="utf-8")
    except OSError:
        사용량글 = ""

    # **D1 을 쓰지 않은 날은 DB 를 읽지 않는다** (docs/infra.md 25.1034). 이 단계는 `if: always()` 라 `DB_BACKEND=auto`
    # 이면 **매일** 돌았고, 따라잡기가 돌지 않은 날(0단계가 turso 라고 판정)에도 Turso 에서 시세·점수·감성을 통째로
    # 셌다 — 하루 수백만 행 `[확인필요: 실측]`. 0단계가 깨져 어느 DB 인지 모르는 날(unknown·빈 값)은 예전처럼 읽는다
    백엔드 = os.environ.get(CATCHUP_BACKEND_ENV, "").strip()
    if 백엔드 and 백엔드 not in ("d1", "unknown"):
        print(f"[복귀 점검] 지금 쓰는 DB 가 {백엔드} 라 따라잡기가 돌지 않았다 — 확인할 것이 없어 DB 를 읽지 않는다")
        return 0

    f = 사실()
    try:
        from batch.core.client import TursoClient

        with TursoClient() as client:
            f = 모으기(client, args.market)
    except Exception as error:  # noqa: BLE001 — 못 읽어도 워크플로에서 아는 것은 찍는다
        print(f"[복귀 점검] DB 를 읽지 못했다: {error}")

    판정들 = 전부(os.environ.get("STEPS_JSON"), os.environ.get("GITHUB_EVENT_NAME"), 사용량글, f)
    print(머리말(판정들))
    for p in 판정들:
        print(p.줄())
    return 0


if __name__ == "__main__":
    sys.exit(main())
